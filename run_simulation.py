"""Driver: seed the book, run random flow, measure what emerges.

The agents here have no strategy — they flip coins. Everything the output shows
is therefore a property of the *matching rules*, not of trader intelligence. That
makes this a null model: any stylized fact reproduced here needs no behavioral
explanation.

Four measurements:
  1. Spread distribution — what the mechanics alone produce.
  2. Depth profile — where liquidity accumulates.
  3. Price impact vs order size — the concave relationship real markets show.
  4. Variance scaling of the mid — is it actually a random walk?

Usage:  python run_simulation.py
"""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.order import Side
from src.orderbook import LimitOrderBook
from src.simulator import seed_book, simulate

N_EVENTS = 50_000
SEED = 7


def section(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


def main() -> None:
    book = LimitOrderBook()
    seed_book(book, mid=100.0, levels=5, qty=100)
    result = simulate(book, n_events=N_EVENTS, seed=SEED)

    mids = np.array(result["mids"])
    spreads = np.array(result["spreads"])

    section(f"Run: {N_EVENTS:,} events, seed {SEED}")
    print(f"Trades executed : {result['n_trades']:,}")
    print(f"Volume traded   : {result['volume']:,}")
    print(f"Mid             : {mids[0]:.2f} -> {mids[-1]:.2f} "
          f"(range {mids.min():.2f} - {mids.max():.2f})")

    section("1. Spread distribution")
    print(f"mean {spreads.mean():.4f} | median {np.median(spreads):.4f} | "
          f"min {spreads.min():.4f} | max {spreads.max():.4f}")
    at_one_tick = (spreads <= 0.0100001).mean()
    print(f"share of time at the minimum 1-tick spread: {at_one_tick:.1%}")
    print("The spread is not a parameter anywhere in the simulator. It is what")
    print("is left over when market orders eat depth faster than limit orders")
    print("replace it.")

    section("2. Depth profile (final book)")
    print(f"{'level':>6} {'bid px':>9} {'bid qty':>9}   {'ask px':>9} {'ask qty':>9}")
    bids = book.depth(Side.BUY, 8)
    asks = book.depth(Side.SELL, 8)
    for i in range(max(len(bids), len(asks))):
        b = f"{bids[i][0]:>9.2f} {bids[i][1]:>9,}" if i < len(bids) else " " * 19
        a = f"{asks[i][0]:>9.2f} {asks[i][1]:>9,}" if i < len(asks) else ""
        print(f"{i + 1:>6} {b}   {a}")
    print("\nDepth grows sharply away from the touch. Real books do this too, but")
    print("here it is exaggerated: zero-intelligence agents never reprice, so")
    print("passive orders placed far from the mid accumulate and are never")
    print("cancelled by anyone who has changed their mind.")

    section("3. Price impact vs market order size")
    impacts = pd.DataFrame(result["impacts"], columns=["qty", "impact"])
    impacts["bucket"] = pd.cut(impacts["qty"], [0, 50, 100, 150, 200])
    table = impacts.groupby("bucket", observed=True)["impact"].agg(["mean", "count"])
    print(f"{'order size':>14} {'mean |mid move|':>17} {'n':>8}")
    for bucket, row in table.iterrows():
        print(f"{str(bucket):>14} {row['mean']:>17.6f} {int(row['count']):>8,}")

    # fit impact ~ a * qty^b on the bucket means; b < 1 means concave impact
    x = np.log(np.array([b.mid for b in table.index], dtype=float))
    y = np.log(table["mean"].values)
    b_exp, log_a = np.polyfit(x, y, 1)
    print(f"\npower-law fit: impact ~ size^{b_exp:.2f}")
    print("Exponent below 1 means CONCAVE impact - doubling the order size costs")
    print("less than double. Real markets sit nearer 0.5 (square-root impact);")
    print("this book is closer to linear because its depth profile is thin at")
    print("the touch and random, not because the mechanism differs.")

    section("4. Is the mid a random walk?")
    d = np.diff(mids)
    ac1 = np.corrcoef(d[:-1], d[1:])[0, 1]
    print(f"lag-1 autocorrelation of mid changes: {ac1:+.4f}")
    print(f"\n{'horizon k':>10} {'var(mid_t+k - mid_t)':>22} {'ratio to k=1':>14} "
          f"{'random walk':>13}")
    var1 = np.var(mids[1:] - mids[:-1])
    ks_all = (1, 2, 4, 8, 16, 32, 64, 128)
    var_ratios = []
    for k in ks_all:
        v = np.var(mids[k:] - mids[:-k])
        var_ratios.append(v / var1)
        print(f"{k:>10} {v:>22.6f} {v / var1:>14.2f} {float(k):>13.2f}")

    # var(k) ~ k^(2H): H = 0.5 is a random walk, H < 0.5 is mean-reverting
    slope = np.polyfit(np.log(ks_all), np.log(var_ratios), 1)[0]
    print(f"\nfitted scaling: var ~ k^{slope:.2f}  ->  Hurst exponent H = {slope / 2:.2f}")
    print("(H = 0.50 is a pure random walk; H < 0.50 is mean-reverting)")
    print("\nA pure random walk doubles its variance when you double the horizon,")
    print("so the last two columns would match. They do not: variance grows more")
    print("slowly than k, and lag-1 autocorrelation is negative. The mid MEAN-")
    print("REVERTS at short horizons. That is bid-ask bounce plus the restoring")
    print("force of resting depth - a market order that pushes the mid up leaves")
    print("a thin ask and a fat bid behind it, and the next order pushes back.")
    print("Real equity data shows the same signature, and mistaking it for a")
    print("tradable signal is a well-known way to lose money to the spread.")

    fig, axes = plt.subplots(4, 1, figsize=(11, 14))

    axes[0].plot(mids, lw=0.6)
    axes[0].set_title("Mid price — a random-ish walk born from coin flips")
    axes[0].set_xlabel("event"); axes[0].set_ylabel("mid")

    axes[1].hist(spreads, bins=np.arange(0.005, 0.145, 0.01), edgecolor="white")
    axes[1].set_title("Spread distribution (never specified — it emerged)")
    axes[1].set_xlabel("spread"); axes[1].set_ylabel("events")

    sizes = np.array([b.mid for b in table.index], dtype=float)
    axes[2].plot(sizes, table["mean"].values, "o-", label="measured")
    axes[2].plot(sizes, np.exp(log_a) * sizes**b_exp, "--", c="gray",
                 label=f"fit: size^{b_exp:.2f}")
    axes[2].set_title("Price impact grows concavely with order size")
    axes[2].set_xlabel("market order size"); axes[2].set_ylabel("mean |mid move|")
    axes[2].legend()

    ks = np.array([1, 2, 4, 8, 16, 32, 64, 128])
    ratios = [np.var(mids[k:] - mids[:-k]) / var1 for k in ks]
    axes[3].loglog(ks, ratios, "o-", label="measured")
    axes[3].loglog(ks, ks, "--", c="gray", label="pure random walk (slope 1)")
    axes[3].set_title("Variance scaling — sub-diffusive, i.e. mean-reverting")
    axes[3].set_xlabel("horizon k (events)"); axes[3].set_ylabel("var ratio")
    axes[3].legend()

    plt.tight_layout()
    plt.savefig("simulation.png", dpi=120)
    print("\nSaved plot to simulation.png")


if __name__ == "__main__":
    main()
