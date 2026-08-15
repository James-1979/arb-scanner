from pathlib import Path

from arbscanner import __version__
from arbscanner.api import _racing_book_analysis_from_sources
from arbscanner.engine import best_strategy_legs, diagnose_equal_return, strategy_book_analysis
from arbscanner.models import Leg

ROOT = Path(__file__).parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_non_positive_strategy_ranks_by_roi_not_smallest_absolute_loss():
    # The 1.50/1.50 pair has tiny depth and therefore a small absolute loss, but
    # its economics are much worse than the deep 1.90/1.90 pair. Diagnostics
    # must report the better ROI book rather than the smallest pound loss.
    candidates = {
        "A": [
            Leg("Betfair delayed", "A", 1.50, 0.50, 0.0),
            Leg("Matchbook", "A", 1.90, 1000.0, 0.0),
        ],
        "B": [
            Leg("Betfair delayed", "B", 1.90, 1000.0, 0.0),
            Leg("Matchbook", "B", 1.50, 0.50, 0.0),
        ],
    }
    legs = best_strategy_legs(candidates, 0.0, require_cross_exchange=True)
    assert [leg.odds for leg in legs] == [1.90, 1.90]
    diag = diagnose_equal_return(legs, 1000.0)
    assert diag["expected_roi_pct"] == -5.0
    analysis = strategy_book_analysis(candidates, 0.0, require_cross_exchange=True)
    assert analysis["selection_basis"] == "best_roi_non_positive"
    assert analysis["selected_cross_exchange_book_pct"] < 106.0


def test_positive_strategy_keeps_profit_first_depth_aware_selection():
    candidates = {
        "A": [
            Leg("Betfair delayed", "A", 2.20, 1.0, 0.0),
            Leg("Matchbook", "A", 2.10, 1000.0, 0.0),
        ],
        "B": [
            Leg("Betfair delayed", "B", 2.10, 1000.0, 0.0),
            Leg("Matchbook", "B", 2.20, 1.0, 0.0),
        ],
    }
    legs = best_strategy_legs(candidates, 0.0, require_cross_exchange=True)
    assert [leg.odds for leg in legs] == [2.10, 2.10]
    analysis = strategy_book_analysis(candidates, 0.0, require_cross_exchange=True)
    assert analysis["selection_basis"] == "positive_profit"
    assert analysis["selected_diagnostic"]["expected_profit"] > 0


def _source(exchange: str, odds: list[float]):
    return {
        "exchange": exchange,
        "event_id": f"{exchange}-event",
        "market_id": f"{exchange}-market",
        "in_play": False,
        "status": "OPEN",
        "runner_prices": [
            {
                "selection_id": f"{exchange}-{trap}",
                "selection": f"Dog {trap}",
                "trap_number": trap,
                "canonical_selection_key": f"trap:{trap}|dog-{trap}",
                "odds": price,
                "liquidity": 100.0,
                "commission_pct": 2.0,
                "commission_source": "test",
            }
            for trap, price in enumerate(odds, start=1)
        ],
    }


def test_racing_book_audit_exposes_exchange_combined_and_selected_books():
    sources = [
        _source("Betfair delayed", [5.0, 5.2, 5.4, 5.6, 5.8]),
        _source("Matchbook", [5.1, 5.1, 5.5, 5.5, 5.9]),
    ]
    result = _racing_book_analysis_from_sources(sources, minimum_liquidity=2.0)
    assert result["valid"] is True
    assert set(result["exchange_books_pct"]) == {"Betfair delayed", "Matchbook"}
    assert result["best_combined_book_pct"] is not None
    assert result["selected_cross_exchange_book_pct"] is not None
    assert len(result["runner_prices"]) == 5
    assert all("Betfair delayed" in row["prices"] for row in result["runner_prices"])
    assert all("Matchbook" in row["prices"] for row in result["runner_prices"])


def test_racing_ui_labels_price_audit_and_pending_state():
    assert __version__ == "0.9.36"
    assert "Theoretical best" in HTML
    assert "Deployable selected" in HTML
    assert "Liquidity prevents the theoretical best price from being deployable" in HTML
    assert "Runner price-side audit" in HTML
    assert "MB raw BACK" in HTML
    assert "MB raw LAY" in HTML
    assert "Diagnostic selection:" in HTML
    assert "PRICE PENDING" in HTML


def test_racing_source_metadata_retains_exact_runner_prices(tmp_path):
    from datetime import datetime, timezone
    from arbscanner.db import DB
    from arbscanner.models import ExchangeMarket, MarketMatch, Quote
    from arbscanner.scanner import Scanner
    from arbscanner.secrets import SecretStore

    now = datetime.now(timezone.utc).isoformat()
    market = ExchangeMarket(
        exchange="Matchbook",
        event_id="mb-e1",
        market_id="mb-m1",
        event_name="Romford",
        market_name="Win",
        start_time=now,
        quotes=[
            Quote(
                exchange="Matchbook", event_id="mb-e1", market_id="mb-m1",
                event_name="Romford", market_name="Win", selection_id="1",
                selection="Dog One", odds=4.2, liquidity=37.5, captured_at=now,
                commission_pct=2.0, commission_source="test", sport="Greyhounds",
                market_type="win", strategy="multi_runner_win", in_play=False,
                market_status="OPEN", section="racing", trap_number=1,
                canonical_selection_key="trap:1|dog-one", runner_status="ACTIVE",
            )
        ],
        market_type="win", strategy="multi_runner_win", sport="Greyhounds",
        in_play=False, section="racing", race_track="romford",
    )
    mm = MarketMatch(
        event_key="romford", market_key="win", display_event="Romford",
        display_market="Win", start_time=now, markets=[market], match_score=1.0,
        market_type="win", strategy="multi_runner_win", sport="Greyhounds",
        in_play=False, section="racing", race_track="romford", runner_count=1,
    )
    scanner = Scanner(DB(tmp_path / "source.sqlite3"), SecretStore())
    sources = scanner._source_markets(mm)
    assert len(sources) == 1
    assert sources[0]["runner_prices"][0]["odds"] == 4.2
    assert sources[0]["runner_prices"][0]["liquidity"] == 37.5
    assert sources[0]["runner_prices"][0]["trap_number"] == 1
