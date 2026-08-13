from src.orderbook import LimitOrderBook
from src.simulator import seed_book, simulate


class TestSimulation:
    def test_runs_and_reports(self):
        book = LimitOrderBook()
        seed_book(book)
        result = simulate(book, n_events=2000, seed=0)
        assert result["n_trades"] > 0
        assert result["volume"] > 0
        assert len(result["mids"]) > 0
        assert all(s >= 0 for s in result["spreads"])

    def test_reproducible_with_seed(self):
        r1 = simulate_fresh(seed=42)
        r2 = simulate_fresh(seed=42)
        assert r1["mids"] == r2["mids"]
        assert r1["n_trades"] == r2["n_trades"]

    def test_different_seeds_differ(self):
        assert simulate_fresh(seed=1)["mids"] != simulate_fresh(seed=2)["mids"]


def simulate_fresh(seed):
    book = LimitOrderBook()
    seed_book(book)
    return simulate(book, n_events=1000, seed=seed)
