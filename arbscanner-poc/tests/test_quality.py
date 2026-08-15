from arbscanner.quality import quality_profile, history_summary


def test_tiny_liquidity_scores_low_even_with_good_deployed_roi():
    sim = {
        "executable": True,
        "deployed": 7.75,
        "expected_profit": 0.06,
        "expected_roi_pct": 0.752,
    }
    q = quality_profile(sim, match_score=0.92, reference_bankroll=500)
    assert q["capital_used_pct"] == 1.55
    assert q["quality_band"] == "Tiny"
    assert q["quality_score"] < 20


def test_full_capacity_profitable_arb_scores_high():
    sim = {
        "executable": True,
        "deployed": 500.0,
        "expected_profit": 3.5,
        "expected_roi_pct": 0.7,
    }
    q = quality_profile(sim, match_score=0.98, reference_bankroll=500)
    assert q["capital_used_pct"] == 100.0
    assert q["bankroll_roi_pct"] == 0.7
    assert q["quality_band"] == "Excellent"
    assert q["quality_score"] >= 80


def test_history_summary():
    rows = [
        {"quality_score": 90, "quality_band": "Excellent", "bankroll_roi_pct": 0.5, "expected_profit": 2.5, "outcome": "A", "realized_pnl": 2.5},
        {"quality_score": 30, "quality_band": "Thin", "bankroll_roi_pct": 0.1, "expected_profit": 0.5, "outcome": None, "realized_pnl": None},
    ]
    s = history_summary(rows)
    assert s["count"] == 2
    assert s["strong_or_better"] == 1
    assert s["average_score"] == 60.0
    assert s["median_bankroll_roi_pct"] == 0.3
    assert s["realized_pnl_total"] == 2.5
