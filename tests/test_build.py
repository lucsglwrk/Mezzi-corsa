"""Prova il calcolo su una piccola rete inventata: casa, Brignole, Sturla, Kennedy, Quarto."""
import datetime as dt
import io
import json
import pathlib
import sys
import tempfile
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_data as b  # noqa: E402

DATE = dt.date(2026, 11, 9)  # lunedi
CAL = ("service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
       "F,1,1,1,1,1,0,0,20260101,20261231\n")


def zip_of(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def clock(m):
    return f"{m // 60:02d}:{m % 60:02d}:00"


def stop_times(trips):
    lines = ["trip_id,arrival_time,departure_time,stop_id,stop_sequence"]
    for tid, calls in trips.items():
        for n, (stop, minute) in enumerate(calls):
            lines.append(f"{tid},{clock(minute)},{clock(minute)},{stop},{n + 1}")
    return "\n".join(lines) + "\n"


def trips_txt(trips):
    return "route_id,service_id,trip_id,trip_headsign,trip_short_name\n" + "".join(
        f"{route},F,{tid},Capolinea,{tid}\n" for tid, route in trips.items())


def network():
    rail_calls, rail_routes = {}, {}
    amt_calls, amt_routes = {}, {}
    for n in range(40):
        s = 360 + n * 30
        rail_calls[f"R{n}"] = [("SES", s), ("BRI", s + 20), ("STU", s + 24), ("QUA", s + 27)]
        rail_routes[f"R{n}"] = "RE"
        rail_calls[f"V{n}"] = [("QUA", s + 5), ("STU", s + 8), ("BRI", s + 12), ("SES", s + 32)]
        rail_routes[f"V{n}"] = "RE"
    for n in range(60):
        s = 360 + n * 20
        amt_calls[f"B{n}"] = [("MEN", s), ("BRG", s + 35), ("STB", s + 50)]
        amt_routes[f"B{n}"] = "L1"
        amt_calls[f"C{n}"] = [("STB", s + 5), ("BRG", s + 20), ("MEN", s + 55)]
        amt_routes[f"C{n}"] = "L1"
        amt_calls[f"K{n}"] = [("BRG", s + 2), ("KEN", s + 10)]
        amt_routes[f"K{n}"] = "L31"
        amt_calls[f"J{n}"] = [("KEN", s + 3), ("BRG", s + 11)]
        amt_routes[f"J{n}"] = "L31"
        amt_calls[f"Q{n}"] = [("KEN", s + 14), ("QUB", s + 40)]
        amt_routes[f"Q{n}"] = "L8"
    amt = zip_of({
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
                     "MEN,Menotti 2/Chiesa,44.4270,8.8480\n"
                     "BRG,Brignole bus,44.4066,8.9460\n"
                     "KEN,Kennedy bus,44.3950,8.9450\n"
                     "STB,Sturla bus,44.3925,8.9792\n"
                     "QUB,Quarto bus,44.3890,8.9950\n",
        "routes.txt": "route_id,route_short_name,route_type\nL1,1,3\nL31,31,3\nL8,8,3\n",
        "trips.txt": trips_txt(amt_routes),
        "stop_times.txt": stop_times(amt_calls),
        "calendar.txt": CAL})
    rail = zip_of({
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
                     "SES,Genova Sestri Ponente,44.4262,8.8520\n"
                     "BRI,Genova Brignole,44.4066,8.9461\n"
                     "STU,Genova Sturla,44.3930,8.9790\n"
                     "QUA,Genova Quarto dei Mille,44.3900,8.9930\n",
        "routes.txt": "route_id,route_short_name,route_type\nRE,R,2\n",
        "trips.txt": trips_txt(rail_routes),
        "stop_times.txt": stop_times(rail_calls),
        "calendar.txt": CAL})
    return amt, rail


class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        amt, rail = network()
        out = pathlib.Path(cls.tmp.name) / "data"
        b.build(amt, rail, b.load_config(), out, None, DATE)
        cls.index = json.loads((out / "index.json").read_text(encoding="utf-8"))
        key = cls.index["dates"][DATE.isoformat()]["day"]
        cls.day = json.loads((out / "days" / f"{key}.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_weekend_escluso(self):
        self.assertNotIn("2026-11-14", self.index["dates"])

    def test_tutte_le_tratte_hanno_soluzioni(self):
        for place in ("sturla", "kennedy", "quarto"):
            self.assertTrue(any(self.day["od"][f"casa>{place}"].values()), place)
            self.assertTrue(any(self.day["od"][f"{place}>casa"].values()), place)

    def test_treno_diretto(self):
        j = self.day["od"]["casa>sturla"]["treno"][0]
        self.assertEqual(len(j[3]), 1)
        self.assertEqual(j[3][0][6] - j[3][0][5], 24)

    def test_misto_con_cambio_per_kennedy(self):
        j = self.day["od"]["casa>kennedy"]["misto"][0]
        self.assertEqual([leg[0] for leg in j[3]], [0, 1])
        self.assertGreaterEqual(j[3][1][5] - j[3][0][6], b.CHANGE_MARGIN["bus"])

    def test_bus_con_due_cambi(self):
        j = self.day["od"]["casa>quarto"]["bus"][0]
        self.assertEqual(len(j[3]), 3)
        for prev, nxt in zip(j[3], j[3][1:]):
            self.assertGreaterEqual(nxt[5] - prev[6], b.CHANGE_MARGIN["bus"])

    def test_orari_coerenti(self):
        for classes in self.day["od"].values():
            for items in classes.values():
                for leave, arrive, _, legs in items:
                    self.assertLess(leave, legs[0][5])
                    self.assertLessEqual(legs[-1][6], arrive)
                    self.assertLessEqual(arrive - leave, b.MAX_DURATION)

    def test_nessuna_soluzione_dominata(self):
        for classes in self.day["od"].values():
            for items in classes.values():
                for x in items:
                    for y in items:
                        if x is not y and len(y[3]) <= len(x[3]):
                            self.assertFalse(y[0] > x[0] and y[1] < x[1])


if __name__ == "__main__":
    unittest.main()
