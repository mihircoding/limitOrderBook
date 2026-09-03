"""Regression tests for the headline findings in RESULTS.md.

Those numbers came from one run of run_simulation.py (50k events, seed 7)
and were never locked in anywhere - a change to the matching engine or the
flow mix could quietly flip "sublinear impact" or "sub-diffusive mid" and
nothing would fail. These pin the qualitative claims down.

The two findings are not equally robust, and the tests below say so instead
of hiding it. Sweeping seed 1-7 at several sample sizes: the Hurst exponent
comes back in a tight 0.31-0.36 band every time, no exceptions. The impact
exponent is noisier - individual seeds land anywhere from 0.84 to 1.23 at
8k events, and even at the reporting scale (50k) an unlucky seed can land
at 0.99 or, rarely, just over 1.0. The mechanism really is concave on
average; any single seed's bucket-mean fit is a small-sample estimate of
it and shouldn't be asserted against on its own.
"""

from src.orderbook import LimitOrderBook
from src.simulator import hurst_exponent, impact_exponent, seed_book, simulate

REPORTING_N_EVENTS = 50_000
REPORTING_SEED = 7  # matches RESULTS.md exactly


def run(n_events=REPORTING_N_EVENTS, seed=REPORTING_SEED):
    book = LimitOrderBook()
    seed_book(book, mid=100.0, levels=5, qty=100)
    return simulate(book, n_events=n_events, seed=seed)


class TestPriceImpactIsConcave:
    def test_matches_results_md_at_the_reporting_seed(self):
        """Same seed and event count as RESULTS.md section 3 - this is the
        number in the table, not an approximation of it."""
        result = run()
        assert impact_exponent(result["impacts"]) < 1.0

    def test_mean_exponent_across_seeds_is_concave(self):
        """A single seed's fit can drift close to, or slightly past, 1.0 -
        see the module docstring. Average a handful of seeds instead of
        demanding every one individually stay under the line."""
        exponents = [impact_exponent(run(seed=s)["impacts"]) for s in range(1, 6)]
        assert sum(exponents) / len(exponents) < 0.98


class TestMidIsSubDiffusive:
    def test_matches_results_md_at_the_reporting_seed(self):
        """RESULTS.md section 4: H = 0.32 at 50k events, seed 7."""
        result = run()
        H = hurst_exponent(result["mids"])
        assert 0.25 < H < 0.40

    def test_holds_across_seeds_no_exceptions(self):
        """Unlike impact concavity, this one doesn't need averaging - it
        held for every seed tried during development, small or large n."""
        for seed in range(1, 6):
            result = run(n_events=8000, seed=seed)
            assert hurst_exponent(result["mids"]) < 0.5


class TestSpreadHasATickFloor:
    def test_never_below_one_tick(self):
        """The book can't cross itself, so the spread has a hard floor at
        one tick (0.01) - RESULTS.md section 1's "min 0.0100 (one tick)"."""
        result = run(n_events=8000, seed=1)
        assert all(s >= 0.0099 for s in result["spreads"])

    def test_spends_meaningful_time_at_the_floor(self):
        """RESULTS.md found 37.9% of events at the 1-tick minimum. Not
        pinning the exact share, just that it's a real fraction of the
        time and not a rare edge case."""
        result = run(n_events=8000, seed=1)
        at_floor = sum(1 for s in result["spreads"] if s <= 0.0100001)
        assert at_floor / len(result["spreads"]) > 0.10
