// Calcolo dei percorsi nel browser sulla rete del giorno (RAPTOR a partenze multiple, max 2 cambi).
(function (g) {
  const WALK_M_PER_MIN = 75, ROAD = 1.3, K = 3;
  const BOARD = [3, 1, 2], CHANGE = [5, 2, 3]; // minuti: treno, bus, metro

  function meters(aLat, aLon, bLat, bLon) {
    const r = Math.PI / 180, dp = (bLat - aLat) * r, dl = (bLon - aLon) * r;
    const h = Math.sin(dp / 2) ** 2 + Math.cos(aLat * r) * Math.cos(bLat * r) * Math.sin(dl / 2) ** 2;
    return 12742000 * Math.asin(Math.sqrt(h));
  }
  const walkMin = (m) => Math.ceil((m * ROAD) / WALK_M_PER_MIN);

  function prepare(raw) {
    const stops = raw.stops.map(([name, lat, lon, code, rail]) => ({ name, lat, lon, code, rail }));
    const pats = raw.pats.map(([mode, line, head, seq, deltas, names]) => {
      const n = seq.length, count = deltas.length / n, times = new Int32Array(deltas.length);
      for (let t = 0; t < count; t++) {
        let v = 0;
        for (let i = 0; i < n; i++) { v += deltas[t * n + i]; times[t * n + i] = v; }
      }
      return { mode, line, head, stops: seq, n, count, times, names: names || null };
    });
    const stopPats = stops.map(() => []);
    pats.forEach((p, pi) => p.stops.forEach((s, i) => { if (i < p.n - 1) stopPats[s].push(pi, i); }));
    return { stops, pats, stopPats, foot: raw.foot };
  }

  // Fermate raggiungibili a piedi da un punto: [{s, w}] con w in minuti.
  function nearby(net, lat, lon) {
    const all = net.stops.map((st, s) => ({ s, d: meters(lat, lon, st.lat, st.lon), rail: st.rail }));
    let near = all.filter((x) => x.d <= (x.rail ? 1200 : 700));
    if (near.length < 3) near = all.filter((x) => x.d <= 3000).sort((a, b) => a.d - b.d).slice(0, 4);
    return near.sort((a, b) => a.d - b.d).slice(0, 30).map((x) => ({ s: x.s, w: walkMin(x.d) }));
  }

  // Prima corsa della linea che passa alla posizione i non prima dell'ora x.
  function lower(p, i, x) {
    let lo = 0, hi = p.count;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (p.times[mid * p.n + i] < x) lo = mid + 1; else hi = mid;
    }
    return lo;
  }

  function rebuild(net, par, k, end, arrive) {
    const legs = [];
    let s = end.s, r = k, guard = 0;
    while (r > 0 && guard++ < 50) {
      const p = par[r][s];
      if (p.from !== undefined) { s = p.from; continue; }
      const pat = net.pats[p.pi];
      legs.unshift({
        mode: pat.mode, line: pat.names ? pat.names[p.trip] : pat.line, head: pat.head,
        from: pat.stops[p.bi], to: pat.stops[p.ai],
        dep: pat.times[p.trip * pat.n + p.bi], arr: pat.times[p.trip * pat.n + p.ai],
      });
      s = pat.stops[p.bi];
      r--;
    }
    const walkIn = par[0][s].w;
    return { leave: legs[0].dep - BOARD[legs[0].mode] - walkIn, arrive, walkIn, walkOut: end.w, legs };
  }

  // Soluzioni non dominate con partenza tra depart e depart+window. modes: Set di modi ammessi o null.
  function search(net, from, to, depart, modes, window = 120, maxLeaves = 150) {
    const acc = nearby(net, from.lat, from.lon), egr = nearby(net, to.lat, to.lon);
    if (!acc.length || !egr.length) return [];
    const S = net.stops.length, tau = [], par = [];
    for (let k = 0; k <= K; k++) { tau.push(new Float64Array(S).fill(Infinity)); par.push(new Array(S)); }
    const ok = (p) => !modes || modes.has(p.mode);

    const leaves = new Set();
    for (const { s, w } of acc) {
      const sp = net.stopPats[s];
      for (let q = 0; q < sp.length; q += 2) {
        const p = net.pats[sp[q]], i = sp[q + 1];
        if (!ok(p)) continue;
        const m = BOARD[p.mode];
        for (let t = lower(p, i, depart + w + m); t < p.count; t++) {
          const leave = p.times[t * p.n + i] - w - m;
          if (leave > depart + window) break;
          leaves.add(leave);
        }
      }
    }
    const order = [...leaves].sort((a, b) => b - a).slice(-maxLeaves);

    const best = new Array(K + 1).fill(Infinity), out = [];
    const bound = (k) => { let b = Infinity; for (let r = 1; r <= k; r++) b = Math.min(b, best[r]); return b; };
    for (const leave of order) {
      let marked = [];
      for (const { s, w } of acc) {
        if (leave + w < tau[0][s]) { tau[0][s] = leave + w; par[0][s] = { w }; marked.push(s); }
      }
      for (let k = 1; k <= K && marked.length; k++) {
        const limit = bound(k), queue = new Map();
        for (const s of marked) {
          const sp = net.stopPats[s];
          for (let q = 0; q < sp.length; q += 2) {
            if (!ok(net.pats[sp[q]])) continue;
            const cur = queue.get(sp[q]);
            if (cur === undefined || sp[q + 1] < cur) queue.set(sp[q], sp[q + 1]);
          }
        }
        const next = new Set();
        for (const [pi, start] of queue) {
          const p = net.pats[pi], m = k === 1 ? BOARD[p.mode] : CHANGE[p.mode];
          let trip = -1, bi = -1;
          for (let i = start; i < p.n; i++) {
            const s = p.stops[i];
            if (trip >= 0) {
              const a = p.times[trip * p.n + i];
              if (a < tau[k][s] && a < limit) { tau[k][s] = a; par[k][s] = { pi, trip, bi, ai: i }; next.add(s); }
            }
            const prev = tau[k - 1][s];
            if (i < p.n - 1 && prev < Infinity && (trip < 0 || prev + m <= p.times[trip * p.n + i])) {
              const et = lower(p, i, prev + m);
              if (et < p.count && (trip < 0 || et < trip)) { trip = et; bi = i; }
            }
          }
        }
        const routed = [...next], bases = routed.map((s) => tau[k][s]);
        routed.forEach((s, n) => {
          const f = net.foot[s];
          for (let q = 0; q < f.length; q += 2) {
            const j = f[q], v = bases[n] + f[q + 1];
            if (v < tau[k][j] && v < limit) { tau[k][j] = v; par[k][j] = { from: s }; next.add(j); }
          }
        });
        marked = [...next];
      }
      for (let k = 1; k <= K; k++) {
        let arrive = Infinity, end = null;
        for (const e of egr) { const v = tau[k][e.s] + e.w; if (v < arrive) { arrive = v; end = e; } }
        if (arrive < bound(k)) { best[k] = arrive; out.push(rebuild(net, par, k, end, arrive)); }
      }
    }
    return out.sort((a, b) => a.leave - b.leave || a.arrive - b.arrive);
  }

  // Tre elenchi: solo treno, solo bus e metro, misti non peggiori delle soluzioni pure.
  function plan(net, from, to, depart, window) {
    const treno = search(net, from, to, depart, new Set([0]), window);
    const bus = search(net, from, to, depart, new Set([1, 2]), window);
    const pure = treno.concat(bus);
    const misto = search(net, from, to, depart, null, window).filter((j) => {
      const trains = j.legs.filter((l) => l.mode === 0).length;
      return trains > 0 && trains < j.legs.length
        && !pure.some((p) => p.leave >= j.leave && p.arrive <= j.arrive);
    });
    return { treno, bus, misto };
  }

  g.Router = { prepare, nearby, search, plan, meters, walkMin };
})(globalThis);
