"""Builds docs/data.js for the GitHub Pages site.

Same 50,000-event run RESULTS.md reports, dumped as JSON so the site plots
the actual simulation output rather than numbers retyped into HTML. Re-run
after changing the matching engine and the charts move with it:

    python docs/build_data.py
"""

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_simulation import N_EVENTS, SEED
from src.order import Side
from src.orderbook import LimitOrderBook
from src.simulator import hurst_exponent, impact_exponent, seed_book, simulate

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
    }

    OUT.write_text("window.DATA = " + json.dumps(data, separators=(",", ":")) + ";\n",
                   encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    print(f"  {result['n_trades']:,} trades | spread mean {data['spread_stats']['mean']} "
          f"| impact^{data['impact']['exponent']} | hurst {data['variance']['hurst']}")


if __name__ == "__main__":
    main()
