import asyncio
from dataclasses import asdict
from pathlib import Path

from arbscanner.db import DB
from arbscanner.engine import simulate_equal_return
from arbscanner.execution import (
    ExecutionState,
    PaperExecutionCoordinator,
    build_execution_plan,
    capital_required_by_exchange_from_fills,
)
from arbscanner.models import Leg, Scenario
from arbscanner.monitor_timing import MonitorTimingObserver, model_execution_inputs


def legs():
    return [
        Leg("Matchbook", "Home", 2.10, 200.0, 2.0, event_id="mb-e", market_id="mb-m", selection_id="h"),
        Leg("Betfair delayed", "Away", 2.10, 200.0, 2.0, event_id="bf-e", market_id="bf-m", selection_id="a"),
    ]


def observation(offset_ms: int, away_liquidity: float = 200.0):
    return {
        "offset_ms": offset_ms,
        "quotes": [
            {"exchange": "Matchbook", "selection": "Home", "odds": 2.10, "liquidity": 200.0},
            {"exchange": "Betfair delayed", "selection": "Away", "odds": 2.10, "liquidity": away_liquidity},
        ],
    }


def test_measured_execution_partial_fill_runs_through_hedge_engine():
    ls = legs()
    sim = simulate_equal_return(ls, Scenario("monitor", 100.0, 100.0, 100.0))
    plan = build_execution_plan(ls, sim, max_unhedged_exposure=5.0)
    fill_fractions, fill_odds, hedge_quotes, decisions = model_execution_inputs(
        plan,
        observation(500, away_liquidity=10.0),
        observation(1000, away_liquidity=200.0),
    )
    assert 0 < fill_fractions[1] < 1
    assert any(x["reason"] == "PARTIAL_LIQUIDITY" for x in decisions)
    result = PaperExecutionCoordinator(balance_tolerance=0.10).execute(
        plan,
        fill_fractions=fill_fractions,
        fill_odds=fill_odds,
        hedge_quotes=hedge_quotes,
        hedge_capital_by_exchange={"matchbook": 200.0, "betfair": 200.0},
    )
    assert result.before_hedge.balanced is False
    assert result.state == ExecutionState.HEDGED
    assert result.after_hedge.balanced is True
    assert any(x.is_hedge for x in result.fills)
    assert capital_required_by_exchange_from_fills(result.fills)


def test_hedge_capacity_can_leave_monitor_position_exposed():
    ls = legs()
    sim = simulate_equal_return(ls, Scenario("monitor", 100.0, 100.0, 100.0))
    plan = build_execution_plan(ls, sim, max_unhedged_exposure=5.0)
    fill_fractions, fill_odds, hedge_quotes, _ = model_execution_inputs(
        plan,
        observation(500, away_liquidity=0.0),
        observation(1000, away_liquidity=200.0),
    )
    result = PaperExecutionCoordinator(balance_tolerance=0.10).execute(
        plan,
        fill_fractions=fill_fractions,
        fill_odds=fill_odds,
        hedge_quotes=hedge_quotes,
        hedge_capital_by_exchange={"matchbook": 200.0, "betfair": 0.0},
    )
    assert result.state == ExecutionState.PANIC
    assert result.after_hedge.balanced is False
    assert any(x["state"] in {"HEDGE_CAPITAL_LIMITED", "HEDGE_REJECTED_NO_CAPITAL"} for x in result.events)


def test_monitor_position_allows_hedge_to_use_reserved_floor(tmp_path: Path):
    db = DB(tmp_path / "reserve.sqlite3")
    db.reset_monitor_wallets({"betfair": 100.0, "matchbook": 100.0})
    oid = db.add_opportunity("evt", "A v B", "2030-01-01T12:00:00+00:00", "Match Odds", 3, 3,
                             [asdict(x) for x in legs()], [], .99, "reserve")
    ok, reason = db.open_monitor_position(
        opportunity_id=oid,
        execution_run_id=None,
        event_key="evt",
        market_name="Match Odds",
        deployed=95.0,
        expected_profit=1.0,
        stakes_by_exchange={"betfair": 55.0, "matchbook": 40.0},
        normal_stakes_by_exchange={"betfair": 35.0, "matchbook": 40.0},
        outcome_exchange_pnls={"Home": {"betfair": 1, "matchbook": 0}, "Away": {"betfair": 0, "matchbook": 1}},
        simulation={},
        hedge_reserve_pct=20.0,
    )
    assert ok, reason
    snap = db.monitor_wallet_snapshot(20.0)
    assert snap["betfair"]["reserved"] == 55.0


class FakeAdapter:
    def __init__(self, name: str):
        self.name = name
        self.calls = 0

    async def fetch_market_state(self, event_id: str, market_id: str):
        self.calls += 1
        partial = self.calls == 3
        if self.name == "Matchbook":
            quotes = {"h": {"odds": 2.10, "liquidity": 200.0}}
        else:
            quotes = {"a": {"odds": 2.10, "liquidity": 8.0 if partial else 200.0}}
        return {
            "ok": True,
            "exchange": self.name,
            "market_id": market_id,
            "status": "OPEN",
            "in_play": False,
            "latency_ms": 1,
            "quotes": quotes,
        }


def test_monitor_timing_observer_opens_position_from_modeled_execution(tmp_path: Path):
    db = DB(tmp_path / "observer.sqlite3")
    db.reset_monitor_wallets({"betfair": 250.0, "matchbook": 250.0})
    ls = legs()
    oid = db.add_opportunity("evt", "A v B", "2030-01-01T12:00:00+00:00", "Match Odds", 3, 3,
                             [asdict(x) for x in ls], [], .99, "observer")
    sim = simulate_equal_return(ls, Scenario("monitor", 100.0, 100.0, 100.0))
    observer = MonitorTimingObserver(db, checkpoints_ms=(1, 2, 3, 4))
    result = asyncio.run(observer.observe(
        opportunity_id=oid,
        original_legs=ls,
        original_simulation=sim,
        adapters=[FakeAdapter("Matchbook"), FakeAdapter("Betfair delayed")],
        event_start="2030-01-01T12:00:00+00:00",
        bankroll=100.0,
        max_bankroll_pct=100.0,
        max_event_exposure_pct=100.0,
        min_roi=0.0,
        min_profit=0.0,
        pre_match_only=True,
        reference_checkpoint_ms=2,
        execution_checkpoint_ms=3,
        hedge_checkpoint_ms=4,
        event_key="evt",
        market_name="Match Odds",
        hedge_reserve_pct=20.0,
        max_unhedged_exposure=5.0,
        balance_tolerance=0.10,
    ))
    assert result["monitor_opened"] is True
    assert result["monitor_execution_result"]["state"] in {"HEDGED", "PANIC"}
    history = db.execution_history(limit=10, mode="monitor")
    assert len(history) == 1
    assert history[0]["execution_type"] == "modeled_monitor"
    assert history[0]["details"]["execution_model"] == "measured_checkpoints"
    assert history[0]["details"]["execution_result"]["fills"]
    settled = db.settle_monitor_position(oid, "Home")
    assert settled["ok"] is True
    settled_history = db.execution_history(limit=10, mode="monitor")
    assert settled_history[0]["state"] == "MONITOR_SETTLED"
    assert abs(float(settled_history[0]["captured_profit"]) - float(settled["realized_pnl"])) < 1e-3


def test_replay_uses_modeled_fill_and_hedge_evidence(tmp_path: Path):
    from arbscanner.api import API

    api = API(tmp_path / "replay-model.sqlite3")
    cfg = api.db.get_setting("config", {})
    cfg.update({
        "monitor_timing_reference_checkpoint_ms": 250,
        "monitor_execution_checkpoint_ms": 500,
        "monitor_hedge_checkpoint_ms": 1000,
        "execution_hedge_reserve_pct": 20.0,
    })
    api.db.set_setting("config", cfg)
    ls = legs()
    oid = api.db.add_opportunity("evt", "A v B", "2026-08-10T12:00:00+00:00", "Match Odds", 3, 3,
                                 [asdict(x) for x in ls], [], .99, "replay-modeled")
    api.db.conn.execute("UPDATE opportunities SET detected_at=? WHERE id=?", ("2026-08-09T10:00:00+00:00", oid))
    api.db.settle(oid, "Home")
    api.db.conn.execute("UPDATE settlements SET settled_at=? WHERE opportunity_id=?", ("2026-08-10T15:00:00+00:00", oid))
    sim = simulate_equal_return(ls, Scenario("monitor", 100.0, 100.0, 100.0))
    run_id = api.db.start_monitor_timing_run(
        oid,
        started_at="2026-08-09T10:00:00+00:00",
        initial_deployed=sim["deployed"],
        initial_profit=sim["expected_profit"],
        initial_roi_pct=sim["expected_roi_pct"],
        planned_stakes=sim["stakes"],
        reference_checkpoint_ms=250,
    )
    for offset, away_liq in ((250, 200.0), (500, 8.0), (1000, 200.0)):
        quotes = [
            {"exchange": "Matchbook", "selection": "Home", "odds": 2.10, "liquidity": 200.0},
            {"exchange": "Betfair delayed", "selection": "Away", "odds": 2.10, "liquidity": away_liq},
        ]
        api.db.add_monitor_timing_observation(
            run_id,
            offset_ms=offset,
            elapsed_ms=offset,
            observed_at=f"2026-08-09T10:00:00.{offset:03d}+00:00",
            fetch_latency_ms=5,
            deployed=sim["deployed"],
            expected_profit=sim["expected_profit"],
            expected_roi_pct=sim["expected_roi_pct"],
            executable_fraction=1.0 if offset != 500 else 0.1,
            full_stake_available=offset != 500,
            still_profitable=True,
            still_executable=offset != 500,
            failure_reason=None if offset != 500 else "INSUFFICIENT_LIQUIDITY",
            quotes=quotes,
            venues=[],
        )
    api.db.finish_monitor_timing_run(
        run_id,
        finished_at="2026-08-09T10:00:01+00:00",
        status="COMPLETE",
        survived_through_ms=250,
        first_failure_reason="INSUFFICIENT_LIQUIDITY",
        reference_profit=sim["expected_profit"],
        reference_roi_pct=sim["expected_roi_pct"],
        reference_executable=True,
    )
    api.db.conn.commit()
    replay = api.analytics_replay({
        "exchange_balances": {"betfair": 50.0, "matchbook": 50.0},
        "from_utc": "2026-08-09T09:00:00+00:00",
        "to_utc": "2026-08-09T11:00:00+00:00",
        "minimum_profit": 0,
        "minimum_deployed_roi_pct": 0,
    })
    assert replay["ok"] is True
    assert replay["result"]["counts"]["taken"] == 1
    event = replay["result"]["events"][0]
    assert event["execution_state"] in {"HEDGED", "PANIC"}
    assert event["modeled_worst_case_pnl"] is not None


def test_frontend_calls_strategy_page_replay_and_version_is_current():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert ">Replay</span>" in html
    assert "<h1>Replay</h1>" in html
    assert "Strategy Replay" not in html
    assert "PoC 0.7.11" in html


def test_opportunity_drawer_exposes_modeled_monitor_execution_summary():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "Monitor execution" in html
    assert "Modelled worst case" in html
    assert "latest_execution" in Path(__file__).parents[1].joinpath("arbscanner", "api.py").read_text()
