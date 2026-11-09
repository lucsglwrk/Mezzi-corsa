#!/usr/bin/env python3
"""Costruisce i percorsi casa <-> luoghi di corsa dagli orari GTFS ufficiali.

Uso:  python scripts/build_data.py --out site/data --report report.txt
      python scripts/build_data.py --amt a.zip --rail t.zip --out ...   (file locali)

Solo libreria standard. Limite voluto: al massimo due cambi.
"""
import argparse
import bisect
import copy
import csv
import datetime as dt
import hashlib
import io
import json
import math
import pathlib
import re
import shutil
import sys
import urllib.request
import zipfile
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
AMT_URLS = ["https://www.amt.genova.it/amt/GTFS/GTFS_AMT_GENOVA.zip"]
CKAN = "https://dati.regione.liguria.it/api/3/action/package_show?id={}"
AMT_DATASET = "ds-641"
RAIL_DATASET = "ds-637"
RAIL_FALLBACK = ("https://srvcarto.regione.liguria.it/dtuff/download_statico/opendata/"
                 "trasporti/GTFS/GTFS-IT-ITC3-TRENITALIA-20260614-20261212-pf.zip")
UA = {"User-Agent": "mezzi-corsa/1.0"}

BOX = (44.36, 44.46, 8.78, 9.03)  # Sestri-Quarto, lungo la costa
WIDE_BOX = (44.36, 44.56, 8.70, 9.12)  # tutta Genova, per la scheda Viaggio
WALK_M_PER_MIN = 75.0             # passo tranquillo
ROAD_FACTOR = 1.3                 # strada reale rispetto alla linea d'aria
TRANSFER_RADIUS_M = 300
MAX_WAIT_TRANSFER = 40            # min
MAX_DURATION = 110                # min, porta a porta
BOARD_MARGIN = {"train": 3, "bus": 1, "metro": 2}  # anticipo in fermata
CHANGE_MARGIN = {"train": 5, "bus": 2, "metro": 3}
HORIZON_DAYS = 28
CODES = {}  # fermata -> codice AMT per i passaggi live


def load_config():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def haversine(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def walk_min(meters):
    return math.ceil(meters * ROAD_FACTOR / WALK_M_PER_MIN)


def to_min(s):
    h, m, *_ = s.strip().split(":")
    return int(h) * 60 + int(m)


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def ckan_zip_urls(dataset):
    """URL degli zip GTFS elencati dalla Regione: cambiano nome a ogni orario."""
    meta = json.loads(fetch(CKAN.format(dataset)))
    urls = [r.get("url", "") for r in meta["result"]["resources"]]
    return [u for u in urls if re.search(r"gtfs.*\.zip$", u, re.I)]


def download(name, candidates):
    errors = []
    for url in candidates:
        try:
            data = fetch(url)
            zipfile.ZipFile(io.BytesIO(data)).testzip()
            print(f"{name}: scaricato {url} ({len(data) // 1024} KB)")
            return data, url
        except Exception as e:  # si prova la fonte successiva
            errors.append(f"{url}: {e}")
    raise SystemExit(f"{name}: nessuna fonte disponibile\n" + "\n".join(errors))


def resolve(dataset, static):
    try:
        found = ckan_zip_urls(dataset)
    except Exception as e:
        print(f"Catalogo {dataset} non raggiungibile: {e}", file=sys.stderr)
        found = []
    return found, static


def rows(z, name):
    if name not in z.namelist():
        return
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig", newline=""))


class Feed:
    """Parte utile di un feed: fermate nel riquadro e corse che vi passano."""

    def __init__(self, data, prefix, modes, box=BOX):
        z = zipfile.ZipFile(io.BytesIO(data))
        self.prefix = prefix
        self.stops = {}
        self.codes = {}
        for r in rows(z, "stops.txt"):
            if r.get("stop_lat") and r.get("stop_lon"):
                pos = (float(r["stop_lat"]), float(r["stop_lon"]))
                if box[0] <= pos[0] <= box[1] and box[2] <= pos[1] <= box[3]:
                    self.stops[prefix + r["stop_id"]] = (r["stop_name"].strip(), pos)
                    self.codes[prefix + r["stop_id"]] = (r.get("stop_code") or r["stop_id"]).strip()
        self.routes = {}
        for r in rows(z, "routes.txt"):
            mode = modes.get(r["route_type"])
            if mode:
                label = (r.get("route_short_name") or r.get("route_long_name") or "").strip()
                self.routes[r["route_id"]] = (mode, label)
        self.trips = {}
        self.base = {}
        for r in rows(z, "trips.txt"):
            route = self.routes.get(r["route_id"])
            if route:
                mode, label = route
                self.base[prefix + r["trip_id"]] = label
                if mode == "train" and r.get("trip_short_name"):
                    label = f"{label} {r['trip_short_name']}".strip()
                self.trips[prefix + r["trip_id"]] = (
                    r["service_id"], mode, label, (r.get("trip_headsign") or "").strip())
        times = defaultdict(list)
        for r in rows(z, "stop_times.txt"):
            trip, stop = prefix + r["trip_id"], prefix + r["stop_id"]
            if trip in self.trips and stop in self.stops:
                arr = r.get("arrival_time") or r.get("departure_time")
                dep = r.get("departure_time") or r.get("arrival_time")
                if arr and dep:
                    times[trip].append((int(r["stop_sequence"]), stop, to_min(arr), to_min(dep)))
        self.times = {t: sorted(v) for t, v in times.items() if len(v) >= 2}
        self.calendar = {}
        for r in rows(z, "calendar.txt"):
            days = [r[d] == "1" for d in ("monday", "tuesday", "wednesday", "thursday",
                                          "friday", "saturday", "sunday")]
            self.calendar[r["service_id"]] = (r["start_date"], r["end_date"], days)
        self.extra = defaultdict(dict)
        for r in rows(z, "calendar_dates.txt"):
            self.extra[r["date"]][r["service_id"]] = r["exception_type"] == "1"

    def active(self, date):
        key = date.strftime("%Y%m%d")
        out = {s for s, (a, b, days) in self.calendar.items()
               if a <= key <= b and days[date.weekday()]}
        for sid, on in self.extra.get(key, {}).items():
            (out.add if on else out.discard)(sid)
        return out

    def trips_on(self, date):
        services = self.active(date)
        return {t for t in self.times if self.trips[t][0] in services}


def narrow(feed, box):
    """Stesso feed, limitato alle fermate dentro un riquadro più piccolo."""
    out = copy.copy(feed)
    out.stops = {k: v for k, v in feed.stops.items()
                 if box[0] <= v[1][0] <= box[1] and box[2] <= v[1][1] <= box[3]}
    out.times = {}
    for trip, seq in feed.times.items():
        kept = [c for c in seq if c[1] in out.stops]
        if len(kept) >= 2:
            out.times[trip] = kept
    return out


def network(feeds, today):
    """Rete compatta di un giorno, per il calcolo dei percorsi nel browser."""
    stops, codes, trips, times, base = {}, {}, {}, {}, {}
    for f in feeds:
        stops.update(f.stops), codes.update(f.codes), trips.update(f.trips)
        times.update(f.times), base.update(f.base)
    used = sorted({c[1] for t in today for c in times[t]})
    idx = {s: n for n, s in enumerate(used)}
    modes = {"train": 0, "bus": 1, "metro": 2}
    pats = defaultdict(list)
    for t in today:
        _, mode, label, head = trips[t]
        seq = times[t]
        key = (modes[mode], base[t], head, tuple(idx[c[1]] for c in seq))
        pats[key].append(([c[3] for c in seq], label))
    out = []
    for (mode, line, head, seq), runs in sorted(pats.items()):
        runs.sort(key=lambda r: r[0])
        deltas = []
        for run, _ in runs:
            deltas.append(run[0])
            deltas.extend(max(0, b - a) for a, b in zip(run, run[1:]))
        out.append([mode, line, head, list(seq), deltas,
                    [label for _, label in runs] if mode == 0 else 0])
    xfer = transfer_map({s: stops[s] for s in used})
    foot = [[v for other, w in xfer.get(s, ()) if other != s for v in (idx[other], w)] for s in used]
    table = [[stops[s][0], round(stops[s][1][0], 5), round(stops[s][1][1], 5),
              "" if s.startswith("t:") else codes.get(s, ""), int(s.startswith("t:"))] for s in used]
    return {"stops": table, "foot": foot, "pats": out}


def find_points(stops, words, near=None):
    """Fermate il cui nome contiene una delle parole, eventualmente vicino a un punto."""
    out = []
    for name, pos in stops.values():
        up = name.upper()
        if any(w in up for w in words) and (near is None or haversine(pos, near) < 2500):
            out.append(pos)
    return out


def centroid(points):
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def resolve_places(cfg, stops):
    """Coordinate dei luoghi: fissate in config oppure ricavate dai nomi delle fermate."""
    places = {}
    for key, p in cfg["places"].items():
        near = tuple(p["near"])
        hits = find_points(stops, [w.upper() for w in p.get("match", [])], near)
        pos = tuple(p["pos"]) if p.get("pos") else centroid(hits) if hits else near
        places[key] = {**p, "pos": pos}
    return places


def access(stops, pos, bus_r, rail_r, rail_ids):
    out = {}
    for sid, (_, sp) in stops.items():
        meters = haversine(pos, sp)
        if meters <= (rail_r if sid in rail_ids else bus_r):
            out[sid] = walk_min(meters)
    return out


def transfer_map(stops):
    cell = {}
    for sid, (_, pos) in stops.items():
        cell.setdefault((int(pos[0] / 0.004), int(pos[1] / 0.004)), []).append(sid)
    out = defaultdict(list)
    for (cx, cy), members in cell.items():
        near = [s for dx in (-1, 0, 1) for dy in (-1, 0, 1) for s in cell.get((cx + dx, cy + dy), [])]
        for a in members:
            for b in near:
                d = haversine(stops[a][1], stops[b][1])
                if d <= TRANSFER_RADIUS_M:
                    out[a].append((b, walk_min(d)))
    return out


def classify(modes):
    kinds = set(modes)
    if kinds == {"train"}:
        return "treno"
    if "train" in kinds:
        return "misto"
    return "bus"


def pareto(items):
    """Tiene una soluzione solo se nessun'altra parte dopo e arriva prima (a pari cambi)."""
    items = sorted(items, key=lambda j: (j["arrive"], -j["leave"], len(j["legs"])))
    best = {1: -1, 2: -1, 3: -1}
    kept = []
    for j in items:
        n = len(j["legs"])
        if j["leave"] <= max(best[k] for k in best if k <= n):
            continue
        best[n] = max(best[n], j["leave"])
        kept.append(j)
    return kept


def leg(trips, trip, seq, i, k, stops):
    _, mode, label, head = trips[trip]
    return {"mode": mode, "line": label, "head": head,
            "from": stops[seq[i][1]][0], "to": stops[seq[k][1]][0],
            "dep": seq[i][3], "arr": seq[k][2], "code": CODES.get(seq[i][1], "")}


def journeys(net, today, origin, dest):
    trips, times, stops, xfer = net["trips"], net["times"], net["stops"], net["xfer"]
    reach, stop_deps, by_stop = {}, defaultdict(list), defaultdict(list)
    for t in today:
        seq = times[t]
        tail, best = [None] * len(seq), None
        for i in range(len(seq) - 1, -1, -1):
            tail[i] = best
            _, s, arr, _ = seq[i]
            if s in dest:
                score = arr + dest[s]
                if best is None or score < best[0]:
                    best = (score, i)
        reach[t] = tail
        for i, (_, s, _, dep) in enumerate(seq[:-1]):
            by_stop[s].append((t, i, dep))
            if tail[i]:
                stop_deps[s].append((dep, t, i))
    for lst in stop_deps.values():
        lst.sort()
    keys = {s: [e[0] for e in lst] for s, lst in stop_deps.items()}

    def suffix(deps):
        """Per ogni fermata: dall'ora x in poi, la coincidenza che arriva prima."""
        table = {}
        for s, lst in deps.items():
            lst.sort(key=lambda e: e[0])
            best, out = None, [None] * len(lst)
            for p in range(len(lst) - 1, -1, -1):
                if best is None or lst[p][1] <= best[1]:
                    best = lst[p]
                out[p] = best
            table[s] = ([e[0] for e in lst], out)
        return table

    def earliest(table, stop, ready, skip):
        entry = table.get(stop)
        if not entry:
            return None
        p = bisect.bisect_left(entry[0], ready)
        if p == len(entry[0]) or entry[1][p][2] == skip:
            return None
        return entry[1][p]

    one_left = defaultdict(list)
    for s, lst in stop_deps.items():
        for dep, u, m in lst:
            one_left[s].append((dep - CHANGE_MARGIN[trips[u][1]], reach[u][m][0], u, m))
    after_one = suffix(one_left)

    # tail2[t][i]: seconda corsa e cambio che portano prima a destinazione dopo la fermata i
    tail2, two_left = {}, defaultdict(list)
    for t in today:
        seq = times[t]
        tail, best = [None] * len(seq), None
        for i in range(len(seq) - 1, -1, -1):
            tail[i] = best
            _, x, arr, _ = seq[i]
            for y, wk in xfer.get(x, ()):
                hit = earliest(after_one, y, arr + wk, t)
                if hit and (best is None or hit[1] < best[0]):
                    best = (hit[1], i, hit[2], hit[3])
        tail2[t] = tail
        for i, (_, s, _, dep) in enumerate(seq[:-1]):
            if tail[i]:
                two_left[s].append((dep - CHANGE_MARGIN[trips[t][1]], tail[i][0], t, i))
    after_two = suffix(two_left)

    found = defaultdict(list)

    def add(legs, walk_in, leave, arrive):
        if arrive - leave <= MAX_DURATION:
            found[classify([l["mode"] for l in legs])].append(
                {"leave": leave, "arrive": arrive, "walk_in": walk_in,
                 "walk_out": None, "legs": legs})

    for o, w0 in origin.items():
        for t, i, dep in by_stop.get(o, ()):
            seq = times[t]
            mode = trips[t][1]
            leave = dep - w0 - BOARD_MARGIN[mode]
            if reach[t][i]:
                arrive, k = reach[t][i]
                add([leg(trips, t, seq, i, k, stops)], w0, leave, arrive)
            for j in range(i + 1, len(seq)):
                x, arr = seq[j][1], seq[j][2]
                for y, wk in xfer.get(x, ()):
                    hit = earliest(after_two, y, arr + wk, t)
                    if hit:
                        arrive, u, m = hit[1], hit[2], hit[3]
                        _, n, v, q = tail2[u][m]
                        _, r = reach[v][q]
                        add([leg(trips, t, seq, i, j, stops),
                             leg(trips, u, times[u], m, n, stops),
                             leg(trips, v, times[v], q, r, stops)], w0, leave, arrive)
                    lst, ks = stop_deps.get(y), keys.get(y)
                    if not lst:
                        continue
                    seen = set()
                    for p in range(bisect.bisect_left(ks, arr + wk), len(lst)):
                        d2, u, m = lst[p]
                        if d2 > arr + wk + MAX_WAIT_TRANSFER:
                            break
                        umode, uline = trips[u][1], trips[u][2]
                        if u == t or uline in seen or d2 < arr + wk + CHANGE_MARGIN[umode]:
                            continue
                        seen.add(uline)
                        arrive, k = reach[u][m]
                        add([leg(trips, t, seq, i, j, stops),
                             leg(trips, u, times[u], m, k, stops)], w0, leave, arrive)

    result = {k: pareto(v) for k, v in found.items()}
    if "misto" in result:
        pure = result.get("treno", []) + result.get("bus", [])
        result["misto"] = [j for j in result["misto"] if not any(
            p["leave"] >= j["leave"] and p["arrive"] <= j["arrive"] for p in pure)]
    return result


def compact(day):
    """Stringhe in tabella unica: i file giornalieri restano piccoli."""
    table, index = [], {}

    def s(v):
        if v not in index:
            index[v] = len(table)
            table.append(v)
        return index[v]

    out = {}
    for od, classes in day.items():
        out[od] = {}
        for cls, items in classes.items():
            out[od][cls] = [
                [j["leave"], j["arrive"], j["walk_in"],
                 [[{"train": 0, "bus": 1, "metro": 2}[l["mode"]], s(l["line"]), s(l["head"]),
                   s(l["from"]), s(l["to"]), l["dep"], l["arr"], l["code"]] for l in j["legs"]]]
                for j in items]
    return {"s": table, "od": out}


def fmt(m):
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def build(amt_data, rail_data, cfg, out_dir, report_path, start=None, sources=None):
    amt_wide = Feed(amt_data, "a:", {"3": "bus", "1": "metro"}, WIDE_BOX)
    rail_wide = Feed(rail_data, "t:", {"2": "train"}, WIDE_BOX)
    amt, rail = narrow(amt_wide, BOX), narrow(rail_wide, BOX)
    stops = {**amt.stops, **rail.stops}
    CODES.update(amt.codes)
    rail_ids = set(rail.stops)
    trips = {**amt.trips, **rail.trips}
    times = {**amt.times, **rail.times}
    used = {s for seq in times.values() for _, s, _, _ in seq}
    stops = {k: v for k, v in stops.items() if k in used}
    net = {"trips": trips, "times": times, "stops": stops, "xfer": transfer_map(stops)}

    places = resolve_places(cfg, stops)
    home = places["casa"]
    home_access = access(stops, home["pos"], home["bus_radius"], home["rail_radius"], rail_ids)
    targets = {k: p for k, p in places.items() if k != "casa"}
    target_access = {k: access(stops, p["pos"], p["bus_radius"], p["rail_radius"], rail_ids)
                     for k, p in targets.items()}

    report = ["# Rapporto dati", f"Fonti: {sources or 'file locali'}", ""]
    for key, p in places.items():
        acc = home_access if key == "casa" else target_access[key]
        names = sorted({stops[s][0] for s in acc})
        report.append(f"{p['name']}: {p['pos'][0]:.5f}, {p['pos'][1]:.5f}, "
                      f"{len(acc)} fermate: {', '.join(names[:14])}")
    report.append("")

    out = pathlib.Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "days").mkdir(parents=True)
    (out / "net").mkdir()
    nets = {}

    start = start or dt.date.today()
    dates, cache = {}, {}
    for offset in range(HORIZON_DAYS):
        date = start + dt.timedelta(days=offset)
        a_trips, r_trips = amt.trips_on(date), rail.trips_on(date)
        if not a_trips and not r_trips:
            continue
        key = hashlib.sha1("\n".join(sorted(a_trips | r_trips)).encode()).hexdigest()[:12]
        if key not in cache:
            today = a_trips | r_trips
            day = {}
            for name, acc in target_access.items():
                day[f"casa>{name}"] = journeys(net, today, home_access, acc)
                day[f"{name}>casa"] = journeys(net, today, acc, home_access)
            (out / "days" / f"{key}.json").write_text(
                json.dumps(compact(day), ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8")
            cache[key] = day
        wide = amt_wide.trips_on(date) | rail_wide.trips_on(date)
        net_key = hashlib.sha1("\n".join(sorted(wide)).encode()).hexdigest()[:12]
        if net_key not in nets:
            text = json.dumps(network([amt_wide, rail_wide], wide), ensure_ascii=False,
                              separators=(",", ":"))
            (out / "net" / f"{net_key}.json").write_text(text, encoding="utf-8")
            nets[net_key] = len(text.encode())
        dates[date.isoformat()] = {"day": key, "net": net_key,
                                   "trains": bool(r_trips), "buses": bool(a_trips)}

    if not dates:
        raise SystemExit("Nessuna corsa valida nei prossimi giorni: orari scaduti?")

    first = next(iter(dates.values()))["day"]
    for od, classes in cache[first].items():
        counts = ", ".join(f"{c}: {len(v)}" for c, v in sorted(classes.items()))
        report.append(f"{od}: {counts or 'NESSUNA SOLUZIONE'}")
    report.append("")
    sample = cache[first].get("casa>quarto", {})
    for cls, items in sorted(sample.items()):
        for j in [j for j in items if 1050 <= j["arrive"] <= 1110][:3]:
            parts = " + ".join(f"{l['line']} {l['from']} {fmt(l['dep'])} > {l['to']} {fmt(l['arr'])}"
                               for l in j["legs"])
            report.append(f"codici fermata: {[l['code'] for l in j['legs']]}")
            report.append(f"esempio {cls}: esci {fmt(j['leave'])}, arrivi {fmt(j['arrive'])}: {parts}")
    report.append(f"reti per la scheda Viaggio: {len(nets)} file, "
                  f"{sum(nets.values()) // 1024} KB in tutto, il più grande {max(nets.values()) // 1024} KB")
    no_rail = [d for d, v in dates.items() if not v["trains"]]
    if no_rail:
        report.append(f"ATTENZIONE: nessun treno negli orari dal {no_rail[0]}")

    index = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
             "dates": dates,
             "places": {k: {"name": p["name"], "pos": p["pos"], "maps": p.get("maps", p["name"])}
                        for k, p in places.items()}}
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    if report_path:
        pathlib.Path(report_path).write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--amt")
    ap.add_argument("--rail")
    ap.add_argument("--out", default="site/data")
    ap.add_argument("--report")
    ap.add_argument("--start", help="AAAA-MM-GG, per le prove")
    args = ap.parse_args()

    sources = None
    if args.amt and args.rail:
        amt = pathlib.Path(args.amt).read_bytes()
        rail = pathlib.Path(args.rail).read_bytes()
    else:
        found, static = resolve(AMT_DATASET, AMT_URLS)
        amt, amt_url = download("AMT", static + found)
        found, _ = resolve(RAIL_DATASET, None)
        rail, rail_url = download("Trenitalia", found + [RAIL_FALLBACK])
        sources = f"{amt_url} ; {rail_url}"
    start = dt.date.fromisoformat(args.start) if args.start else None
    build(amt, rail, load_config(), args.out, args.report, start, sources)


if __name__ == "__main__":
    main()
