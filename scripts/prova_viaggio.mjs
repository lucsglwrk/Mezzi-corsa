// Prova il calcolo della scheda Viaggio sugli orari appena generati e lo scrive nel rapporto.
import fs from "node:fs";
await import("../site/router.js");
const R = globalThis.Router;
const index = JSON.parse(fs.readFileSync("site/data/index.json", "utf8"));
const first = Object.values(index.dates)[0];
const raw = fs.readFileSync(`site/data/net/${first.net}.json`, "utf8");
let t0 = Date.now();
const net = R.prepare(JSON.parse(raw));
const f = (m) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
console.log(`\n# Prova scheda Viaggio\nrete: ${net.stops.length} fermate, ${net.pats.length} percorsi di linea, ${Math.round(raw.length / 1024)} KB, preparata in ${Date.now() - t0} ms`);
const cases = [["Via Menotti > Piazza De Ferrari, ore 08:00", [44.4265, 8.85], [44.4071, 8.934], 480],
  ["Brignole > Nervi, ore 18:00", [44.4066, 8.9461], [44.3815, 9.04], 1080],
  ["Marassi > Pegli, ore 07:30", [44.417, 8.953], [44.426, 8.813], 450]];
for (const [name, a, b, dep] of cases) {
  t0 = Date.now();
  const p = R.plan(net, { lat: a[0], lon: a[1] }, { lat: b[0], lon: b[1] }, dep, 120);
  console.log(`${name}: treno ${p.treno.length}, bus ${p.bus.length}, misto ${p.misto.length} (${Date.now() - t0} ms)`);
  for (const c of ["treno", "bus", "misto"]) {
    const j = p[c][0];
    if (j) console.log(`  ${c}: ${f(j.leave)}-${f(j.arrive)}, ${j.walkIn}' a piedi, ` + j.legs.map((l) => `${l.line} ${net.stops[l.from].name} ${f(l.dep)} > ${net.stops[l.to].name} ${f(l.arr)}`).join(" + ") + `, ${j.walkOut}' a piedi`);
  }
}
