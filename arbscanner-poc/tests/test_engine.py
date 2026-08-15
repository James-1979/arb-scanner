from arbscanner.engine import arb_edge, simulate_equal_return
from arbscanner.models import Leg, Scenario


def fixture_legs():
    return [
        Leg("Matchbook", "Home", 2.72, 420.0, 2.0),
        Leg("Betfair delayed", "Draw", 3.75, 265.0, 2.0),
        Leg("Matchbook", "Away", 3.05, 180.0, 2.0),
    ]


def test_arb_positive_after_commission():
    legs = fixture_legs()
    assert arb_edge(legs) > 0
    sim = simulate_equal_return(legs, Scenario("base", 500))
    assert sim["executable"] is True
    assert sim["expected_profit"] > 0
    assert len(sim["outcome_pnls"]) == 3
    assert min(sim["outcome_pnls"].values()) == sim["expected_profit"]


def test_liquidity_caps_large_bankroll():
    legs = fixture_legs()
    small = simulate_equal_return(legs, Scenario("small", 500))
    large = simulate_equal_return(legs, Scenario("large", 50000))
    assert small["deployed"] <= 500
    assert large["deployed"] < 50000
    assert large["limited_by"] == "liquidity"


def test_strategy_combination_requires_two_exchanges():
    from arbscanner.engine import best_strategy_legs
    candidates = {
        "Home": [
            Leg("Betfair delayed", "Home", 2.60, 100.0, 2.0),
            Leg("Matchbook", "Home", 2.55, 100.0, 2.0),
        ],
        "Draw": [
            Leg("Betfair delayed", "Draw", 3.80, 100.0, 2.0),
            Leg("Matchbook", "Draw", 3.70, 100.0, 2.0),
        ],
        "Away": [
            Leg("Betfair delayed", "Away", 3.20, 100.0, 2.0),
            Leg("Matchbook", "Away", 3.10, 100.0, 2.0),
        ],
    }
    legs = best_strategy_legs(candidates, minimum_liquidity=2.0, require_cross_exchange=True)
    assert len(legs) == 3
    assert len({l.exchange for l in legs}) >= 2


def test_strategy_combination_respects_minimum_liquidity():
    from arbscanner.engine import best_strategy_legs
    candidates = {
        "Home": [Leg("Betfair delayed", "Home", 2.60, 0.01, 2.0), Leg("Matchbook", "Home", 2.50, 20.0, 2.0)],
        "Draw": [Leg("Betfair delayed", "Draw", 3.70, 20.0, 2.0), Leg("Matchbook", "Draw", 3.60, 20.0, 2.0)],
        "Away": [Leg("Betfair delayed", "Away", 3.20, 20.0, 2.0), Leg("Matchbook", "Away", 3.10, 20.0, 2.0)],
    }
    legs = best_strategy_legs(candidates, minimum_liquidity=2.0, require_cross_exchange=True)
    assert len(legs) == 3
    assert all(l.liquidity >= 2.0 for l in legs)
    assert next(l for l in legs if l.selection == "Home").exchange == "Matchbook"
