// Ponte per i dati live di Mezzi Corsa. Si incolla cosi' com'e' in un Cloudflare Worker.
const ALLOWED_ORIGIN = "https://lucsglwrk.github.io";
const VT = "http://www.viaggiatreno.it/infomobilita/resteasy/viaggiatreno";
const AMT = "https://www.amt.genova.it/amt/servizi/passaggi_i.php?CodiceFermata=";
const MAX_BUS = 20;
const MAX_TRAIN = 6;
const TTL = 20;

async function cached(url, ttl = TTL) {
  const cache = globalThis.caches?.default;
  const key = new Request(url);
  const hit = cache && (await cache.match(key));
  const res = hit || (await fetch(url, { headers: { "User-Agent": "Mozilla/5.0" } }));
  if (!res.ok) throw new Error(`upstream ${res.status}`);
  const text = await res.clone().text();
  if (cache && !hit) {
    await cache.put(key, new Response(text, { headers: { "Cache-Control": `max-age=${ttl}` } }));
  }
  return text;
}

const strip = (html) => html.replace(/<[^>]*>/g, " ").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();

export function parseBus(html) {
  const out = [];
  for (const row of html.match(/<tr[\s\S]*?<\/tr>/gi) || []) {
    const cells = (row.match(/<td[\s\S]*?<\/td>/gi) || []).map(strip);
    if (cells.length < 3) continue;
    const time = cells.find((c) => /^\d{2}:\d{2}(:\d{2})?$/.test(c));
    if (!time) continue;
    const prev = cells.find((c) => /^\d+\s*'$/.test(c));
    out.push({
      line: cells[0].replace(/^0+(?=.)/, ""),
      dest: cells[1],
      time: time.slice(0, 5),
      min: prev ? parseInt(prev, 10) : null,
    });
  }
  return out;
}

export function romeDate(date = new Date()) {
  const f = (o) => new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Rome", ...o }).format(date);
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Rome", weekday: "short", month: "short", day: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(date).map((p) => [p.type, p.value]));
  const offset = f({ timeZoneName: "longOffset" }).match(/GMT([+-]\d{2}):?(\d{2})?/);
  const zone = offset ? `${offset[1]}${offset[2] || "00"}` : "+0100";
  return `${parts.weekday} ${parts.month} ${parts.day} ${parts.year} ${parts.hour}:${parts.minute}:${parts.second} GMT${zone}`;
}

export function parseStationCode(text, name) {
  const lines = text.trim().split("\n").map((l) => l.trim().split("|"));
  const hit = lines.find((l) => l[0] === name) || lines[0];
  return hit && hit[1] ? hit[1].trim() : null;
}

export function parseTrains(json) {
  return json.map((t) => ({
    number: String(t.numeroTreno),
    delay: t.ritardo ?? 0,
    started: !t.nonPartito,
    platform: t.binarioEffettivoPartenzaDescrizione?.trim() || t.binarioProgrammatoPartenzaDescrizione?.trim() || null,
    cancelled: t.provvedimento === 1,
  }));
}

// ViaggiaTreno e' solo http: niente Cache API, basta una memoria breve nel Worker.
const memory = new Map();
async function plain(url, ttl) {
  const hit = memory.get(url);
  if (hit && hit.until > Date.now()) return hit.text;
  const res = await fetch(url, { headers: { "User-Agent": "Mozilla/5.0" } });
  const text = await res.text();
  if (!res.ok) throw new Error(`ViaggiaTreno ${res.status}: ${text.slice(0, 120)}`);
  memory.set(url, { text, until: Date.now() + ttl * 1000 });
  return text;
}

async function bus(code) {
  return parseBus(await cached(AMT + code));
}

async function train(name) {
  const list = await plain(`${VT}/autocompletaStazione/${encodeURIComponent(name)}`, 86400);
  const code = parseStationCode(list, name);
  if (!code) throw new Error("stazione non trovata");
  const when = encodeURIComponent(romeDate());
  return parseTrains(JSON.parse(await plain(`${VT}/partenze/${code}/${when}`, TTL)));
}

async function collect(keys, load, errors) {
  const entries = await Promise.all(keys.map(async (k) => {
    try { return [k, await load(k)]; } catch (e) { errors[k] = String(e.message || e); return [k, null]; }
  }));
  return Object.fromEntries(entries);
}

export async function handle(request) {
  const url = new URL(request.url);
  const cors = {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  };
  if (request.method !== "GET" || url.pathname !== "/live") {
    return new Response('{"error":"not found"}', { status: 404, headers: cors });
  }
  const buses = (url.searchParams.get("bus") || "").split(",").filter((c) => /^\d{1,5}$/.test(c)).slice(0, MAX_BUS);
  const trains = (url.searchParams.get("train") || "").split("|").filter((n) => /^[A-Z0-9' .-]{3,40}$/.test(n)).slice(0, MAX_TRAIN);
  const errors = {};
  const [bus_, train_] = await Promise.all([collect(buses, bus, errors), collect(trains, train, errors)]);
  return new Response(JSON.stringify({ bus: bus_, train: train_, errors, at: new Date().toISOString() }), { headers: cors });
}

export default { fetch: handle };
