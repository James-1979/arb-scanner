import asyncio
from dataclasses import asdict
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.engine import simulate_equal_return
from arbscanner.models import Leg, Scenario
from arbscanner.monitor_timing import MonitorTimingObserver

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


class StaticAdapter:
    def __init__(self, name: str):
        self.name = name
        self.calls = 0

    async def fetch_market_state(self, event_id: str, market_id: str):
        self.calls += 1
        if self.name == "Matchbook":
            quotes = {"h": {"odds": 2.10, "liquidity": 500.0}}
        else:
            quotes = {"a": {"odds": 2.10, "liquidity": 500.0}}
        return {
            "ok": True,
            "exchange": self.name,
            "event_id": event_id,
            "market_id": market_id,
            "status": "OPEN",
            "in_play": False,
            "latency_ms": 1,
            "captured_at": "2026-08-11T18:50:00+00:00",
            "quotes": quotes,
        }


def _legs():
    return [
        Leg("Matchbook", "Home", 2.10, 500.0, 2.0, event_id="mb-e", market_id="mb-m", selection_id="h"),
        Leg("Betfair delayed", "Away", 2.10, 500.0, 2.0, event_id="bf-e", market_id="bf-m", selection_id="a"),
    ]


def test_v0828_version_defaults_and_live_boundary(tmp_path):
    assert __version__ == "0.9.36"
    api = API(tmp_path / "boundary.sqlite3")
    engine = api.db.engine_instance("SPORTS_SUPERBET_ARB_PRIMARY")
    assert engine["requested_lifecycle"] == "DISABLED"
    assert engine["active_config"]["config"]["max_tranches"] == 3
    assert engine["active_config"]["config"]["min_depth_multiplier"] >= 1.0
    racing = api.racing_overview({})
    assert racing["monitor_execution_allowed"] is True
    assert racing["live_execution_allowed"] is False
    assert "LIVE order placement remains hard-locked" in HTML


def test_superbet_settings_accept_unlimited_and_arbitrary_n(tmp_path):
    api = API(tmp_path / "settings.sqlite3")
    current = dict(api.db.engine_active_config("SPORTS_SUPERBET_ARB_PRIMARY")["config"])
    current.update({
        "max_tranches": "unlimited", "tranche_size_mode": "fixed", "tranche_size": 35,
        "max_total_stake": 500, "min_net_edge": 1.5, "min_depth_multiplier": 1.4, "recheck_delay_ms": 0,
    })
    saved = api.engine_create_config({"engine_instance_id": "SPORTS_SUPERBET_ARB_PRIMARY", "config": current})
    assert saved["ok"] is True
    assert saved["config"]["config"]["max_tranches"] == "unlimited"
    current["max_tranches"] = 7
    saved = api.engine_create_config({"engine_instance_id": "SPORTS_SUPERBET_ARB_PRIMARY", "config": current})
    assert saved["config"]["config"]["max_tranches"] == 7


def test_superbet_monitor_executes_child_tranches_and_settles_as_one_parent(tmp_path):
    db = DB(tmp_path / "superbet.sqlite3")
    db.reset_monitor_wallets({"betfair": 250.0, "matchbook": 250.0}, stream="pre_match")
    legs = _legs()
    oid = db.add_opportunity(
        "superbet-event",
        "Home v Away",
        "2030-01-01T12:00:00+00:00",
        "Match Winner",
        2.10,
        2.10,
        [asdict(x) for x in legs],
        [],
        0.95238,
        "superbet-test",
        strategy="two-way",
        sport="Tennis",
    )
    sim = simulate_equal_return(legs, Scenario("monitor", 100.0, 100.0, 100.0))
    observer = MonitorTimingObserver(db, checkpoints_ms=(1, 2, 3, 4))
    result = asyncio.run(observer.observe(
        opportunity_id=oid,
        original_legs=legs,
        original_simulation=sim,
        adapters=[StaticAdapter("Matchbook"), StaticAdapter("Betfair delayed")],
        event_start="2030-01-01T12:00:00+00:00",
        bankroll=500.0,
        max_bankroll_pct=20.0,
        max_event_exposure_pct=100.0,
        min_roi=0.0,
        min_profit=0.0,
        pre_match_only=True,
        reference_checkpoint_ms=2,
        execution_checkpoint_ms=3,
        hedge_checkpoint_ms=4,
        event_key="superbet-event",
        market_name="Match Winner",
        hedge_reserve_pct=20.0,
        max_unhedged_exposure=5.0,
        balance_tolerance=0.10,
        monitor_stream="pre_match",
        scaled_entry_enabled=True,
        scaled_entry_max_tranches=3,
        scaled_entry_tranche_size_mode="base",
        scaled_entry_max_total_stake=300.0,
        scaled_entry_min_net_edge=0.0,
        scaled_entry_min_depth_multiplier=1.0,
        scaled_entry_recheck_delay_ms=0,
        scaled_entry_global_bankroll_pct=100.0,
    ))
    assert result["monitor_opened"] is True
    history = db.execution_history(limit=10, mode="monitor")
    assert len(history) == 1
    sb = history[0]["details"]["scaled_entry"]
    assert sb["is_scaled_entry"] is True
    assert sb["tranche_count"] == 3
    assert sb["stop_reason"] == "max_tranches"
    assert len(sb["tranches"]) == 3
    assert all(t["fill_rate_pct"] == 100.0 for t in sb["tranches"])
    assert all(t["fresh_snapshot"].get("quotes") for t in sb["tranches"])
    # Paper-consumed depth at the same quote is removed before each fresh tranche.
    mb_depths = [next(q["liquidity"] for q in t["fresh_snapshot"]["quotes"] if q["exchange"] == "Matchbook") for t in sb["tranches"]]
    assert mb_depths == [500.0, 450.0, 400.0]
    assert len(db.monitor_open_positions(stream="pre_match")) == 1

    settled = db.settle_monitor_position(oid, "Home")
    assert settled and settled["ok"] is True
    settled_history = db.execution_history(limit=10, mode="monitor")
    settled_sb = settled_history[0]["details"]["scaled_entry"]
    assert settled_sb["incremental_realized_pnl"] > 0
    assert abs(settled_sb["total_realized_pnl"] - float(settled["realized_pnl"])) < 1e-4
    summary = db.scaled_entry_summary()
    assert summary["scaled_positions"] == 1
    assert summary["total_tranches"] == 3
    assert summary["average_tranche_fill_rate_pct"] == 100.0




class EdgeDecayAdapter(StaticAdapter):
    async def fetch_market_state(self, event_id: str, market_id: str):
        self.calls += 1
        odds = 2.10 if self.calls <= 4 else 1.90
        if self.name == "Matchbook":
            quotes = {"h": {"odds": odds, "liquidity": 500.0}}
        else:
            quotes = {"a": {"odds": odds, "liquidity": 500.0}}
        return {"ok": True, "exchange": self.name, "event_id": event_id, "market_id": market_id, "status": "OPEN", "in_play": False, "latency_ms": 1, "quotes": quotes}


def test_superbet_stops_before_second_tranche_when_fresh_edge_disappears(tmp_path):
    db = DB(tmp_path / "superbet-edge-stop.sqlite3")
    db.reset_monitor_wallets({"betfair": 250.0, "matchbook": 250.0}, stream="pre_match")
    legs = _legs()
    oid = db.add_opportunity("edge-stop", "Home v Away", "2030-01-01T12:00:00+00:00", "Match Winner", 2.10, 2.10, [asdict(x) for x in legs], [], 0.95238, "edge-stop", strategy="two-way", sport="Tennis")
    base = simulate_equal_return(legs, Scenario("base", 500.0, 5.0, 100.0))
    result = asyncio.run(MonitorTimingObserver(db, checkpoints_ms=(1, 2, 3, 4)).observe(
        opportunity_id=oid, original_legs=legs, original_simulation=base,
        adapters=[EdgeDecayAdapter("Matchbook"), EdgeDecayAdapter("Betfair delayed")],
        event_start="2030-01-01T12:00:00+00:00", bankroll=500.0, max_bankroll_pct=5.0,
        max_event_exposure_pct=100.0, min_roi=0.0, min_profit=0.0, pre_match_only=True,
        reference_checkpoint_ms=2, execution_checkpoint_ms=3, hedge_checkpoint_ms=4,
        event_key="edge-stop", market_name="Match Winner", hedge_reserve_pct=20.0,
        max_unhedged_exposure=5.0, balance_tolerance=0.10, monitor_stream="pre_match",
        scaled_entry_enabled=True, scaled_entry_max_tranches=3, scaled_entry_tranche_size_mode="base",
        scaled_entry_max_total_stake=100.0, scaled_entry_min_net_edge=1.0,
        scaled_entry_min_depth_multiplier=1.0, scaled_entry_recheck_delay_ms=0,
        scaled_entry_global_bankroll_pct=100.0,
    ))
    assert result["monitor_opened"] is True
    sb = db.execution_history(limit=1, mode="monitor")[0]["details"]["scaled_entry"]
    assert sb["is_scaled_entry"] is False
    assert sb["tranche_count"] == 1
    assert sb["stop_reason"] == "price_moved"

def test_superbet_fixed_tranche_can_scale_above_base_without_bypassing_global_limits(tmp_path):
    db = DB(tmp_path / "superbet-fixed.sqlite3")
    db.reset_monitor_wallets({"betfair": 250.0, "matchbook": 250.0}, stream="pre_match")
    legs = _legs()
    oid = db.add_opportunity("fixed-event", "Home v Away", "2030-01-01T12:00:00+00:00", "Match Winner", 2.10, 2.10, [asdict(x) for x in legs], [], 0.95238, "superbet-fixed", strategy="two-way", sport="Tennis")
    base = simulate_equal_return(legs, Scenario("base", 500.0, 5.0, 100.0))
    assert base["deployed"] == 25.0
    result = asyncio.run(MonitorTimingObserver(db, checkpoints_ms=(1, 2, 3, 4)).observe(
        opportunity_id=oid, original_legs=legs, original_simulation=base,
        adapters=[StaticAdapter("Matchbook"), StaticAdapter("Betfair delayed")],
        event_start="2030-01-01T12:00:00+00:00", bankroll=500.0, max_bankroll_pct=5.0,
        max_event_exposure_pct=100.0, min_roi=0.0, min_profit=0.0, pre_match_only=True,
        reference_checkpoint_ms=2, execution_checkpoint_ms=3, hedge_checkpoint_ms=4,
        event_key="fixed-event", market_name="Match Winner", hedge_reserve_pct=20.0,
        max_unhedged_exposure=5.0, balance_tolerance=0.10, monitor_stream="pre_match",
        scaled_entry_enabled=True, scaled_entry_max_tranches=3, scaled_entry_tranche_size_mode="fixed",
        scaled_entry_tranche_size=50.0, scaled_entry_max_total_stake=125.0,
        scaled_entry_min_net_edge=0.0, scaled_entry_min_depth_multiplier=1.0,
        scaled_entry_recheck_delay_ms=0, scaled_entry_global_bankroll_pct=100.0,
    ))
    assert result["monitor_opened"] is True
    sb = db.execution_history(limit=1, mode="monitor")[0]["details"]["scaled_entry"]
    assert sb["is_scaled_entry"] is True
    assert sb["tranche_count"] == 3
    assert sb["base_tranche_stake"] == 25.0
    assert sb["effective_extra_tranche_stake"] == 50.0
    assert sb["total_stake"] == 125.0
    assert sb["stop_reason"] == "max_tranches"

def test_v0828_frontend_contract_for_superbets_consistent_rows_and_viewport_fit():
    for marker in (
        "SCALED ENTRY ·",
        "function scaledEntryDetailHtml",
        "Tranche audit",
        "function positionRowHtml",
        'id="sportsOpenPositions" class="sports-open-list betrows"',
        'id="racingOpenPositions" class="racing-open-list betrows"',
        "function fitDashboardToViewport",
        "height:calc(100dvh - 60px)",
        "Dashboard layout is fully automatic",
        "Theoretical best book",
        "Deployable selected book",
        "Liquidity prevents the theoretical best price from being deployable",
    ):
        assert marker in HTML
    assert "superbet_enabled" not in HTML
    assert "Sports SuperBet ARB" in HTML
