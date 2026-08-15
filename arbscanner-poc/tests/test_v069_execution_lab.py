from arbscanner.engine import simulate_equal_return
from arbscanner.execution import (
    CapitalLedger,
    ExecutionState,
    Fill,
    OrderSide,
    PaperExecutionCoordinator,
    build_execution_plan,
    position_snapshot,
    stress_test_plan,
)
from arbscanner.models import Leg, Scenario


def fixture_legs():
    return [
        Leg("Matchbook", "Home", 2.72, 420.0, 2.0),
        Leg("Betfair delayed", "Draw", 3.75, 265.0, 2.0),
        Leg("Matchbook", "Away", 3.05, 180.0, 2.0),
    ]


def fixture_plan(bankroll=500.0):
    legs = fixture_legs()
    sim = simulate_equal_return(legs, Scenario("execution", bankroll))
    assert sim["executable"]
    return build_execution_plan(
        legs,
        sim,
        opportunity_id=123,
        event_name="Home v Away",
        market_name="Match Odds",
    ), sim


def test_execution_plan_is_paper_only_and_uses_simulated_stakes():
    plan, sim = fixture_plan()
    assert plan.live_execution_allowed is False
    assert plan.opportunity_id == 123
    assert len(plan.legs) == 3
    assert round(sum(x.requested_stake for x in plan.legs), 4) == round(sim["deployed"], 4)
    assert all(x.side == OrderSide.BACK for x in plan.legs)


def test_full_fill_matches_existing_engine_outcome_pnls():
    plan, sim = fixture_plan()
    result = PaperExecutionCoordinator(balance_tolerance=0.10).execute(plan)
    assert result.state == ExecutionState.COMPLETE
    assert result.after_hedge.balanced
    for outcome, pnl in sim["outcome_pnls"].items():
        assert abs(result.after_hedge.outcome_pnls[outcome] - pnl) < 0.02


def test_partial_fill_is_detected_then_hedged_back_to_near_flat():
    plan, _ = fixture_plan()
    result = PaperExecutionCoordinator(balance_tolerance=0.10).execute(
        plan,
        fill_fractions={0: 0.40},
    )
    assert result.before_hedge.balanced is False
    assert result.before_hedge.pnl_spread > 1.0
    assert result.state == ExecutionState.HEDGED
    assert result.after_hedge.balanced is True
    assert result.after_hedge.exposure_spread <= 0.10
    assert result.hedge_instructions


def test_rejected_leg_is_hedged_without_live_order_methods():
    plan, _ = fixture_plan()
    result = PaperExecutionCoordinator(balance_tolerance=0.10).execute(
        plan,
        fill_fractions={1: 0.0},
    )
    assert result.state == ExecutionState.HEDGED
    assert result.after_hedge.balanced
    assert any(x.selection == plan.legs[1].selection for x in result.hedge_instructions)


def test_duplicate_fill_id_is_idempotent_for_position_calculation():
    fill = Fill(
        fill_id="same-fill",
        client_order_id="order-1",
        leg_index=0,
        exchange="Matchbook",
        selection="Yes",
        side=OrderSide.BACK,
        odds=2.0,
        stake=10.0,
    )
    once = position_snapshot(("Yes", "No"), [fill])
    twice = position_snapshot(("Yes", "No"), [fill, fill])
    assert once.outcome_pnls == twice.outcome_pnls


def test_capital_ledger_keeps_hedge_reserve_out_of_normal_orders():
    plan, _ = fixture_plan(100.0)
    exchanges = {leg.exchange for leg in plan.legs}
    ledger = CapitalLedger({exchange: 100.0 for exchange in exchanges}, hedge_reserve_pct=20.0)
    snap = ledger.snapshot()
    assert all(v["hedge_reserve_floor"] == 20.0 for v in snap.values())
    ok, _ = ledger.can_reserve(plan)
    assert ok
    assert ledger.reserve(plan)
    after = ledger.snapshot()
    assert all(v["free_for_hedge"] >= v["hedge_reserve_floor"] - 1e-6 for v in after.values())
    ledger.release(plan.id)
    assert all(v["reserved_normal"] == 0.0 for v in ledger.snapshot().values())


def test_stress_test_includes_full_partial_rejected_and_slippage_cases():
    plan, _ = fixture_plan()
    rows = stress_test_plan(plan)
    names = [row["name"] for row in rows]
    assert names[0] == "all legs fill"
    assert any("40% fill" in name for name in names)
    assert any("rejected" in name for name in names)
    assert any("hedge price worsens" in name for name in names)
    assert all(row["state"] in {"COMPLETE", "HEDGED", "PANIC"} for row in rows)
