from dataclasses import asdict
from pathlib import Path

from arbscanner.api import API
from arbscanner.engine import simulate_equal_return
from arbscanner.execution import capital_by_exchange, exchange_outcome_pnls, fit_simulation_to_wallets
from arbscanner.models import Leg, Scenario


def legs():
    return [
        Leg("Matchbook", "Home", 2.72, 420, 2.0, event_id="mb-e", market_id="mb-m", selection_id="4"),
        Leg("Betfair delayed", "Draw", 3.75, 265, 2.0, event_id="bf-e", market_id="bf-m", selection_id="2"),
        Leg("Betfair delayed", "Away", 3.05, 180, 2.0, event_id="bf-e", market_id="bf-m", selection_id="3"),
    ]


def add_settled_with_monitor_evidence(api: API, detected="2026-08-09T10:00:00+00:00"):
    ls = legs()
    oid = api.db.add_opportunity(
        "evt", "Alpha v Beta", "2026-08-09T12:00:00+00:00", "Match Odds", 2.0, 2.0,
        [asdict(x) for x in ls], [], 0.99, "sig-v078",
    )
    api.db.conn.execute("UPDATE opportunities SET detected_at=? WHERE id=?", (detected, oid))
    api.db.settle(oid, "Home")
    api.db.conn.execute("UPDATE settlements SET settled_at=? WHERE opportunity_id=?", ("2026-08-09T15:00:00+00:00", oid))
    sim = simulate_equal_return(ls, Scenario("monitor", 500, 100, 100))
    rid = api.db.start_monitor_timing_run(
        oid, started_at=detected, initial_deployed=sim["deployed"], initial_profit=sim["expected_profit"],
        initial_roi_pct=sim["expected_roi_pct"], planned_stakes=sim["stakes"], reference_checkpoint_ms=250,
    )
    quotes = [
        {"exchange": x.exchange, "selection": x.selection, "odds": x.odds, "liquidity": x.liquidity}
        for x in ls
    ]
    api.db.add_monitor_timing_observation(
        rid, offset_ms=250, elapsed_ms=270, observed_at="2026-08-09T10:00:00.270+00:00",
        fetch_latency_ms=20, deployed=sim["deployed"], expected_profit=sim["expected_profit"],
        expected_roi_pct=sim["expected_roi_pct"], executable_fraction=1.0, full_stake_available=True,
        still_profitable=True, still_executable=True, failure_reason=None, quotes=quotes, venues=[],
    )
    api.db.finish_monitor_timing_run(
        rid, finished_at="2026-08-09T10:00:00.300+00:00", status="COMPLETE", survived_through_ms=250,
        first_failure_reason=None, reference_profit=sim["expected_profit"], reference_roi_pct=sim["expected_roi_pct"],
        reference_executable=True,
    )
    api.db.conn.commit()
    return oid


def test_monitor_wallets_reserve_and_settle_per_exchange(tmp_path: Path):
    api = API(tmp_path / "wallets.sqlite3")
    api.db.reset_monitor_wallets({"betfair": 100.0, "matchbook": 80.0})
    ls = legs()
    base = simulate_equal_return(ls, Scenario("monitor", 180, 100, 100))
    sim, _ = fit_simulation_to_wallets(base, {"betfair": 80, "matchbook": 64}, total_bankroll=180)
    stakes = capital_by_exchange(sim)
    pnls = exchange_outcome_pnls(ls, sim)
    oid = api.db.add_opportunity("evt", "A v B", "2026-08-10T12:00:00+00:00", "Match Odds", 2, 2,
                                 [asdict(x) for x in ls], [], .99, "wallet-sig")
    ok, reason = api.db.open_monitor_position(
        opportunity_id=oid, execution_run_id=None, event_key="evt", market_name="Match Odds",
        deployed=sim["deployed"], expected_profit=sim["expected_profit"], stakes_by_exchange=stakes,
        outcome_exchange_pnls=pnls, simulation=sim, hedge_reserve_pct=0,
    )
    assert ok, reason
    snap = api.db.monitor_wallet_snapshot(0)
    assert abs(snap["betfair"]["reserved"] - stakes["betfair"]) < 1e-3
    assert abs(snap["matchbook"]["reserved"] - stakes["matchbook"]) < 1e-3
    settled = api.db.settle_monitor_position(oid, "Home")
    assert settled["ok"] is True
    after = api.db.monitor_wallet_snapshot(0)
    assert after["betfair"]["reserved"] == 0
    assert after["matchbook"]["reserved"] == 0
    assert round(after["betfair"]["equity"] + after["matchbook"]["equity"], 4) == round(180 + settled["realized_pnl"], 4)


def test_strategy_replay_uses_monitor_evidence_and_exchange_balances(tmp_path: Path):
    api = API(tmp_path / "replay.sqlite3")
    add_settled_with_monitor_evidence(api)
    r = api.analytics_replay({
        "exchange_balances": {"betfair": 15.0, "matchbook": 85.0},
        "from_utc": "2026-08-09T09:00:00+00:00", "to_utc": "2026-08-09T11:00:00+00:00",
        "minimum_deployed_roi_pct": 0, "minimum_profit": 0, "max_event_exposure_pct": 100,
    })
    assert r["ok"] is True
    x = r["result"]
    assert x["starting_capital"] == 100.0
    assert x["counts"]["taken"] == 1
    assert x["counts"]["exchange_balance_limited"] == 1
    event = x["events"][0]
    assert event["exchange_stakes"]["betfair"] <= 15.0 + 1e-6
    assert event["exchange_stakes"]["matchbook"] <= 85.0 + 1e-6
    assert r["evidence_comparison"]["monitor"]["ending_capital"] == x["ending_capital"]


def test_strategy_replay_rejects_when_required_venue_has_zero_balance(tmp_path: Path):
    api = API(tmp_path / "replay-zero.sqlite3")
    add_settled_with_monitor_evidence(api)
    r = api.analytics_replay({
        "exchange_balances": {"betfair": 0.0, "matchbook": 100.0},
        "minimum_deployed_roi_pct": 0, "minimum_profit": 0,
    })
    assert r["result"]["counts"]["taken"] == 0
    assert r["result"]["counts"]["skipped_exchange_balance"] == 1


def test_reset_monitor_balances_refuses_open_position(tmp_path: Path):
    api = API(tmp_path / "reset.sqlite3")
    api.db.reset_monitor_wallets({"betfair": 100, "matchbook": 100})
    ls = legs(); base = simulate_equal_return(ls, Scenario("m", 100, 100, 100)); sim, _ = fit_simulation_to_wallets(base, {"betfair": 100, "matchbook": 100}, total_bankroll=200)
    oid = api.db.add_opportunity("evt", "A v B", "2026-08-10T12:00:00+00:00", "Match Odds", 2, 2, [asdict(x) for x in ls], [], .99, "reset-sig")
    ok, _ = api.db.open_monitor_position(opportunity_id=oid, execution_run_id=None, event_key="evt", market_name="Match Odds", deployed=sim["deployed"], expected_profit=sim["expected_profit"], stakes_by_exchange=capital_by_exchange(sim), outcome_exchange_pnls=exchange_outcome_pnls(ls, sim), simulation=sim, hedge_reserve_pct=0)
    assert ok
    result = api.reset_monitor_balances({"balances": {"betfair": 50, "matchbook": 50}})
    assert result["ok"] is False
    assert "open" in result["message"].lower()


def test_frontend_uses_monitor_actual_strategy_replay_and_balance_controls():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert ">Strategy Replay</span>" in html
    assert 'id="replayBetfairBalance"' in html and 'id="replayMatchbookBalance"' in html
    assert 'id="monitorBfStart"' in html and 'id="monitorMbStart"' in html
    assert 'id="cmpMonitorEnd"' in html and 'id="cmpActualEnd"' in html
    assert 'id="cmpPotentialEnd"' not in html
    assert "ACTIVATE MONITOR_TIMING" not in html
    assert "Archive &amp; reset trading history" in html
    assert "Monitor timing" in html
