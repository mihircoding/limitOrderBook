"""Builds docs/data.js for the GitHub Pages site.

Same 50,000-event run RESULTS.md reports, plus the latency_study.py sweep,
dumped as JSON so the site plots the actual simulation output rather than
numbers retyped into HTML. Re-run
after changing the matching engine and the charts move with it:

    python docs/build_data.py
"""

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark import (EagerCancelBook, ScanBook, latency_percentiles,
                       time_quotes, time_simulation, tombstone_census)
import latency_study
from run_simulation import N_EVENTS, SEED
from src.order import Side
from src.orderbook import LimitOrderBook
from src.simulator import hurst_exponent, impact_exponent, seed_book, simulate

BENCH_DEPTHS = (10, 50, 100, 500, 1000)
BENCH_EVENTS = 20_000   # smaller than benchmark.py's 50k so the page build stays quick

OUT = Path(__file__).resolve().parent / "data.js"


def main():
    print(f"simulating {N_EVENTS:,} events...")
    book = LimitOrderBook()
    seed_book(book, mid=100.0, levels=5, qty=100)
    result = simulate(book, n_events=N_EVENTS, seed=SEED)

    mids = np.array(result["mids"])
    spreads = np.array(result["spreads"])
    impacts = result["impacts"]

    # 1. spread distribution, in ticks
    ticks = Counter(int(round(s / 0.01)) for s in spreads)
    spread_hist = [[t, ticks[t]] for t in sorted(ticks) if t <= 14]

    # 2. final depth profile
    bids = book.depth(Side.BUY, 8)
    asks = book.depth(Side.SELL, 8)

    # 3. price impact by size bucket
    buckets = [(1, 50), (51, 100), (101, 150), (151, 200)]
    impact_rows = []
    for lo, hi in buckets:
        moves = [m for q, m in impacts if lo <= q <= hi]
        impact_rows.append({"lo": lo, "hi": hi, "n": len(moves),
                            "mean": round(float(np.mean(moves)), 6)})
    exponent = impact_exponent(impacts)

    # 4. variance scaling of the mid
    diffs_1 = np.diff(mids)
    var1 = float(np.var(diffs_1, ddof=1))
    var_rows = []
    for k in (1, 2, 4, 8, 16, 32, 64, 128):
        d = mids[k:] - mids[:-k]
        var_rows.append({"k": k, "var": round(float(np.var(d, ddof=1)), 9),
                         "ratio": round(float(np.var(d, ddof=1) / var1), 3)})
    hurst = hurst_exponent(list(mids))
    acf1 = float(np.corrcoef(diffs_1[:-1], diffs_1[1:])[0, 1])

    print(f"benchmarking ({len(BENCH_DEPTHS)} depths x 2 implementations)...")
    perf = {"events": BENCH_EVENTS, "depths": [], "latency": {}}
    for levels in BENCH_DEPTHS:
        perf["depths"].append({
            "levels": levels,
            "scan_ns": round(time_quotes(ScanBook, levels, 50_000), 1),
            "heap_ns": round(time_quotes(LimitOrderBook, levels, 50_000), 1),
            "scan_eps": round(time_simulation(ScanBook, levels, BENCH_EVENTS)),
            "heap_eps": round(time_simulation(LimitOrderBook, levels, BENCH_EVENTS)),
        })
    lazy_lat = latency_percentiles(LimitOrderBook, 500)
    eager_lat = latency_percentiles(EagerCancelBook, 500)
    perf["latency"] = {op: {k: round(v, 2) for k, v in st.items()}
                       for op, st in lazy_lat.items()}
    perf["cancel"] = {
        "eager": {k: round(v, 2) for k, v in eager_lat["cancel"].items()},
        "lazy": {k: round(v, 2) for k, v in lazy_lat["cancel"].items()},
        "census": tombstone_census(LimitOrderBook, 500),
    }

    print(f"latency study ({len(latency_study.SLOW_LATENCIES)} races)...")
    races = []
    for slow_us in latency_study.SLOW_LATENCIES:
        r = latency_study.run(slow_us)
        row = {"slow_us": slow_us}
        for name in ("fast", "slow"):
            m = r[name]
            row[name] = {
                "pnl": round(m["pnl"] / latency_study.TICK),   # whole run, in ticks
                "passive_volume": m["passive_volume"],
                "active_volume": m["active_volume"],
                "toxic_share": round(m["toxic_share"], 4),
                "passive_ticks": round(m["passive_ticks"], 3),
                "active_ticks": round(m["active_ticks"], 3),
            }
        races.append(row)
    latency = {
        "fast_us": latency_study.FAST_US,
        "rounds": latency_study.N_ROUNDS,
        "quote_size": latency_study.QUOTE_SIZE,
        "p_jump": latency_study.P_JUMP,
        "jump_ticks": latency_study.JUMP_TICKS,
        "taker_us": latency_study.TAKER_LATENCY_US,
        "races": races,
    }

    data = {
        "meta": {"events": N_EVENTS, "seed": SEED,
                 "trades": result["n_trades"], "volume": result["volume"]},
        "mid": [round(float(m), 4) for m in mids[::25]],
        "spread_stats": {
            "mean": round(float(spreads.mean()), 4),
            "median": round(float(np.median(spreads)), 4),
            "min": round(float(spreads.min()), 4),
            "max": round(float(spreads.max()), 4),
            "at_one_tick": round(float((spreads <= 0.0100001).mean()), 4),
        },
        "spread_hist": spread_hist,
        "depth": {"bids": [[p, q] for p, q in bids], "asks": [[p, q] for p, q in asks]},
        "impact": {"rows": impact_rows, "exponent": round(float(exponent), 3)},
        "variance": {"rows": var_rows, "hurst": round(float(hurst), 3),
                     "acf1": round(acf1, 4)},
        "perf": perf,
        "latency": latency,
    }

    OUT.write_text("window.DATA = " + json.dumps(data, separators=(",", ":")) + ";\n",
                   encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    print(f"  {result['n_trades']:,} trades | spread mean {data['spread_stats']['mean']} "
          f"| impact^{data['impact']['exponent']} | hurst {data['variance']['hurst']}")
    deep = perf["depths"][-1]
    print(f"  perf at {deep['levels']} levels: scan {deep['scan_eps']:,} ev/s -> "
          f"heap {deep['heap_eps']:,} ev/s")
    par, gap = races[0], races[1]
    print(f"  latency: parity {par['fast']['pnl']:,} / {par['slow']['pnl']:,} ticks, "
          f"{gap['slow_us']:.0f}us {gap['fast']['pnl']:,} / {gap['slow']['pnl']:,}")


if __name__ == "__main__":
    main()
