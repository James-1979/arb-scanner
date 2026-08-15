from __future__ import annotations

from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API, _racing_book_analysis_from_sources
from arbscanner.engine import diagnose_equal_return, simulate_equal_return
from arbscanner.models import Leg, Scenario

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def leg(exchange: str, selection: str, odds: float, liquidity: float = 1000.0, commission: float = 2.0) -> Leg:
    return Leg(exchange=exchange, selection=selection, odds=odds, liquidity=liquidity, commission_pct=commission)


def test_two_way_commission_aware_solver_equalises_net_pnl():
    legs = [leg("Betfair delayed", "A", 2.2, commission=2.0), leg("Matchbook", "B", 2.2, commission=3.0)]
    result = simulate_equal_return(legs, Scenario("test", 1000.0))
    assert result["executable"] is True
    assert result["staking_method"] == "commission_aware_net_equal_return"
    assert result["net_equalized"] is True
    assert result["net_pnl_spread"] <= 1e-6
    values = list(result["outcome_pnls"].values())
    assert max(values) - min(values) <= 0.0001
    assert result["expected_profit"] == min(values)
    assert result["commission_by_outcome"]
    assert result["gross_outcome_pnls"]


def test_three_way_commission_aware_solver_equalises_net_pnl():
    legs = [
        leg("Betfair delayed", "Home", 3.6, commission=2.0),
        leg("Matchbook", "Draw", 3.8, commission=3.0),
        leg("Betfair delayed", "Away", 3.5, commission=2.0),
    ]
    result = simulate_equal_return(legs, Scenario("test", 1000.0))
    assert result["executable"] is True
    assert result["net_equalized"] is True
    assert len(result["outcome_pnls"]) == 3
    assert max(result["outcome_pnls"].values()) - min(result["outcome_pnls"].values()) <= 0.0001


def test_romford_five_runner_regression_is_positive_after_commission_aware_rebalance():
    # Exact selected BACK prices from the user's v0.9.0 Romford 17:12 audit.
    legs = [
        leg("Matchbook", "Baileys Bullet", 10.00, 2.45, 2.0),
        leg("Betfair delayed", "Alans Amigo", 3.55, 12.06, 2.0),
        leg("Betfair delayed", "Crystal Nancy", 3.45, 13.69, 2.0),
        leg("Matchbook", "Innfield Melody", 8.60, 2.28, 2.0),
        leg("Matchbook", "Hawkfield Grant", 5.00, 12.07, 2.0),
    ]
    result = diagnose_equal_return(legs, 500.0)
    assert result["valid"] is True
    assert result["theoretical_edge_pct"] > 1.0
    assert result["staking_method"] == "commission_aware_net_equal_return"
    assert result["net_equalized"] is True
    assert result["expected_roi_pct"] > 0.0
    assert result["net_pnl_spread"] <= 1e-6
    assert len(result["outcome_pnls"]) == 5
    assert min(result["outcome_pnls"].values()) > 0.0


def test_gross_positive_but_post_commission_negative_is_not_executable():
    legs = [leg("Betfair delayed", "A", 2.00, commission=5.0), leg("Matchbook", "B", 2.01, commission=5.0)]
    result = simulate_equal_return(legs, Scenario("thin", 1000.0))
    assert result["theoretical_edge_pct"] > 0.0
    assert result["net_equalized"] is True
    assert result["expected_profit"] < 0.0
    assert result["executable"] is False
    assert "Commission-aware net P&L" in result["reason"]


def test_commission_aware_solver_respects_liquidity_limit():
    legs = [leg("Betfair delayed", "A", 2.2, liquidity=20.0, commission=2.0), leg("Matchbook", "B", 2.2, liquidity=1000.0, commission=2.0)]
    result = simulate_equal_return(legs, Scenario("limited", 1000.0))
    assert result["executable"] is True
    assert result["limited_by"] == "liquidity"
    stakes = {x["selection"]: x["stake"] for x in result["stakes"]}
    assert stakes["A"] <= 20.0001


def test_historical_clean_fill_audit_flags_gross_positive_net_negative():
    row = {
        "outcome": "A",
        "final_pnl": -0.3,
        "legs": [{"selection": "A"}, {"selection": "B"}],
        "details": {"execution_result": {"fills": [
            {"exchange": "Betfair delayed", "selection": "A", "stake": 10.0, "odds": 2.0, "commission_pct": 5.0, "side": "BACK", "is_hedge": False},
            {"exchange": "Matchbook", "selection": "B", "stake": 9.8, "odds": 2.05, "commission_pct": 5.0, "side": "BACK", "is_hedge": False},
        ], "events": []}},
    }
    audit = API._settled_commission_audit(row)
    assert audit["available"] is True
    assert audit["clean"] is True
    assert audit["gross_pnl"] == 0.2
    assert audit["commission"] == 0.5
    assert audit["model_net_pnl"] == -0.3
    assert audit["post_commission_negative"] is True
    assert audit["commission_erosion"] is True


def _source(exchange: str, rows: list[tuple[int | None, str, float, float]], raw_sides: bool = False):
    prices = []
    for idx, (trap, name, odds, liquidity) in enumerate(rows, start=1):
        raw = []
        if raw_sides:
            raw = [
                {"side": "back", "odds": odds, "available_amount": liquidity},
                {"side": "lay", "odds": odds + 0.2, "available_amount": max(0.1, liquidity / 3)},
            ]
        prices.append({
            "selection_id": f"{exchange}-{idx}", "selection": name, "trap_number": trap,
            "canonical_selection_key": f"trap:{trap}|{name.lower()}" if trap else f"name:{name.lower()}",
            "odds": odds, "liquidity": liquidity, "commission_pct": 2.0, "commission_source": "test",
            "interpreted_source_side": "back" if exchange == "Matchbook" else "availableToBack", "raw_prices": raw,
        })
    return {"exchange": exchange, "event_id": exchange + "-e", "market_id": exchange + "-m", "status": "OPEN", "in_play": False, "runner_prices": prices}


def test_racing_source_analysis_exposes_commission_aware_outcome_audit():
    dogs = ["Baileys Bullet", "Alans Amigo", "Crystal Nancy", "Innfield Melody", "Hawkfield Grant"]
    bf_odds = [6.00, 3.55, 3.45, 5.80, 3.95]
    bf_liq = [10.21, 12.06, 13.69, 15.07, 17.45]
    mb_odds = [10.00, 3.35, 3.25, 8.60, 5.00]
    mb_liq = [2.45, 10.00, 6.87, 2.28, 12.07]
    bf = _source("Betfair delayed", [(i, n, o, l) for i, (n, o, l) in enumerate(zip(dogs, bf_odds, bf_liq), 1)])
    mb = _source("Matchbook", [(None, n.lower(), o, l) for n, o, l in zip(dogs, mb_odds, mb_liq)], raw_sides=True)
    result = _racing_book_analysis_from_sources([bf, mb], minimum_liquidity=2.0)
    assert result["valid"] is True
    diag = result["selected_diagnostic"]
    assert diag["staking_method"] == "commission_aware_net_equal_return"
    assert diag["net_equalized"] is True
    assert len(diag["outcome_pnls"]) == 5
    assert max(diag["outcome_pnls"].values()) - min(diag["outcome_pnls"].values()) <= 0.0001
    assert diag["commission_by_outcome"]


def test_frontend_exposes_commission_transparency_and_filters():
    assert "Gross P&amp;L" in HTML
    assert "Commission" in HTML
    assert "Net P&amp;L" in HTML
    assert "Commission erosion" in HTML
    assert "Gross + / net -" in HTML
    assert "POST-COMMISSION NEGATIVE" in HTML
    assert "Post-commission outcome check" in HTML
    assert "COMMISSION-AWARE NET EQUAL RETURN" in HTML


def test_v0825_version_and_execution_locks():
    assert __version__ == "0.9.36"
    assert "PoC 0.9.36" in HTML
    assert "MONITOR only." in HTML
    assert "LIVE order placement remains hard-locked" in HTML
