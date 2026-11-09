import test from "node:test";
import assert from "node:assert/strict";
await import("../site/router.js");
const { prepare, search, plan, walkMin } = globalThis.Router;

// Casa(0) --bus 1--> Piazza(1) ... Stazione(2) --treno--> Mare(3); Piazza e Stazione a 3 min a piedi.
const row = (start, gaps, n) => Array.from({ length: n }, (_, t) => [start + t * 30, ...gaps]).flat();
const raw = {
  stops: [["Casa", 44.4270, 8.8480, "0001", 0], ["Piazza", 44.4100, 8.9000, "0002", 0],
    ["Stazione", 44.4110, 8.9010, "", 1], ["Mare", 44.3930, 8.9790, "", 1], ["Mare bus", 44.3931, 8.9791, "0004", 0]],
  foot: [[], [2, 3], [1, 3], [4, 1], [3, 1]],
  pats: [[1, "1", "Piazza", [0, 1], row(600, [20], 6), 0],
    [0, "R", "Levante", [2, 3], row(630, [10], 6), ["R 1", "R 2", "R 3", "R 4", "R 5", "R 6"]],
    [1, "15", "Mare", [1, 4], row(625, [40], 6), 0]],
};
const net = prepare(raw);
const casa = { lat: 44.4270, lon: 8.8480 }, mare = { lat: 44.3930, lon: 8.9790 };

test("trova bus + treno con il cambio a piedi", () => {
  const all = search(net, casa, mare, 590, null);
  const j = all.find((x) => x.legs.length === 2 && x.legs[1].mode === 0);
  assert.ok(j);
  assert.deepEqual(j.legs.map((l) => [l.line, l.dep, l.arr]), [["1", 600, 620], ["R 1", 630, 640]]);
  assert.equal(j.leave, 599);
  assert.equal(j.arrive, 640);
});

test("rispetta il margine di cambio e l'ora di partenza", () => {
  for (const j of search(net, casa, mare, 590, null)) {
    assert.ok(j.leave >= 590);
    j.legs.slice(1).forEach((l, i) => assert.ok(l.dep - j.legs[i].arr >= 2));
  }
  assert.ok(search(net, casa, mare, 601, null).every((j) => j.legs[0].dep >= 630));
});

test("divide per mezzo e scarta le soluzioni dominate", () => {
  const p = plan(net, casa, mare, 590);
  assert.equal(p.treno.length, 0);
  assert.ok(p.bus.length > 0 && p.bus.every((j) => j.legs.every((l) => l.mode !== 0)));
  assert.ok(p.misto.length > 0);
  assert.equal(p.bus[0].arrive, 665 + walkMin(14));
  const list = search(net, casa, mare, 590, null);
  list.forEach((a) => list.forEach((b) => {
    if (a !== b && b.legs.length <= a.legs.length) assert.ok(!(b.leave >= a.leave && b.arrive < a.arrive));
  }));
});

test("nessuna fermata vicina, nessun risultato", () => {
  assert.deepEqual(search(net, { lat: 45.5, lon: 9.2 }, mare, 590, null), []);
});
