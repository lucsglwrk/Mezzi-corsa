import test from "node:test";
import assert from "node:assert/strict";
import { handle, parseBus, parseStationCode, parseTrains, romeDate } from "../worker/worker.js";

const HTML = `<table><tr><th>Linea</th><th>Destinazione</th><th>Orario</th><th>Prev</th></tr>
<tr><td><a href="#">014</a></td><td>BRIGNOLE FS</td><td>11:04:10</td><td>10'</td></tr>
<tr><td>N1</td><td>VOLTRI</td><td>11:14:06</td><td></td></tr></table>`;

test("legge i passaggi AMT", () => {
  assert.deepEqual(parseBus(HTML), [
    { line: "14", dest: "BRIGNOLE FS", time: "11:04", min: 10 },
    { line: "N1", dest: "VOLTRI", time: "11:14", min: null }]);
  assert.deepEqual(parseBus("<table><tr><td>Linea</td></tr></table>"), []);
});

test("legge stazioni e treni", () => {
  assert.equal(parseStationCode("GENOVA NERVI|S04711\nGENOVA STURLA|S04709\n", "GENOVA STURLA"), "S04709");
  assert.deepEqual(parseTrains([{ numeroTreno: 12397, ritardo: 7, nonPartito: false, binarioProgrammatoPartenzaDescrizione: "2 ", provvedimento: 0 }]),
    [{ number: "12397", delay: 7, started: true, platform: "2", cancelled: false }]);
});

test("data nel formato atteso da ViaggiaTreno", () => {
  assert.equal(romeDate(new Date("2026-11-06T10:00:00Z")), "Fri Nov 06 2026 11:00:00 GMT+0100");
  assert.equal(romeDate(new Date("2026-07-06T10:00:00Z")), "Mon Jul 06 2026 12:00:00 GMT+0200");
});

test("risponde solo con parametri validi e isola i guasti", async () => {
  const calls = [];
  globalThis.fetch = async (u) => { calls.push(u); if (String(u).includes("0002")) return new Response("x", { status: 500 }); return new Response(HTML); };
  const r = await handle(new Request("https://x.dev/live?bus=0001,0002,evil/../x"));
  const j = await r.json();
  assert.equal(j.bus["0001"].length, 2);
  assert.equal(j.bus["0002"], null);
  assert.equal(calls.length, 2);
  assert.equal((await handle(new Request("https://x.dev/altro"))).status, 404);
});
