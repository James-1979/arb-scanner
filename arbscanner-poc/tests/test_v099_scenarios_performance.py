from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from arbscanner.api import API
from arbscanner.engine import simulate_equal_return
from arbscanner.models import Leg, Scenario
from arbscanner.replay import prepare_replay_history, replay_analysis

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _legs():
    return [
        Leg("Matchbook", "Home", 2.72, 420, 2.0, event_id="mb-e", market_id="mb-m", selection_id="4"),
        Leg("Betfair delayed", "Draw", 3.75, 265, 2.0, event_id="bf-e", market_id="bf-m", selection_id="2"),
        Leg("Betfair delayed", "Away", 3.05, 180, 2.0, event_id="bf-e", market_id="bf-m", selection_id="3"),
    ]


def _add_settled_with_monitor_evidence(api: API, suffix: str = "a", detected: str = "2026-08-09T10:00:00+00:00"):
    legs = _legs()
    oid = api.db.add_opportunity(
        f"evt-{suffix}", f"Alpha {suffix} v Beta {suffix}", "2026-08-09T12:00:00+00:00", "Match Odds", 2.0, 2.0,
        [asdict(x) for x in legs], [], 0.99, f"sig-v099-scenario-{suffix}",
    )
    api.db.conn.execute("UPDATE opportunities SET detected_at=? WHERE id=?", (detected, oid))
    api.db.settle(oid, "Home")
    api.db.conn.execute("UPDATE settlements SET settled_at=? WHERE opportunity_id=?", ("2026-08-09T15:00:00+00:00", oid))
    sim = simulate_equal_return(legs, Scenario("monitor", 500, 100, 100))
    rid = api.db.start_monitor_timing_run(
        oid, started_at=detected, initial_deployed=sim["deployed"], initial_profit=sim["expected_profit"],
        initial_roi_pct=sim["expected_roi_pct"], planned_stakes=sim["stakes"], reference_checkpoint_ms=250,
    )
    quotes = [
        {"exchange": leg.exchange, "selection": leg.selection, "odds": leg.odds, "liquidity": leg.liquidity}
        for leg in legs
    ]
    for offset in (250, 500, 1000):
        api.db.add_monitor_timing_observation(
            rid, offset_ms=offset, elapsed_ms=offset + 10, observed_at=f"2026-08-09T10:00:00.{offset:03d}+00:00",
            fetch_latency_ms=10, deployed=sim["deployed"], expected_profit=sim["expected_profit"],
            expected_roi_pct=sim["expected_roi_pct"], executable_fraction=1.0, full_stake_available=True,
            still_profitable=True, still_executable=True, failure_reason=None, quotes=quotes, venues=[],
        )
    api.db.finish_monitor_timing_run(
        rid, finished_at="2026-08-09T10:00:01.100+00:00", status="COMPLETE", survived_through_ms=1000,
        first_failure_reason=None, reference_profit=sim["expected_profit"], reference_roi_pct=sim["expected_roi_pct"],
        reference_executable=True,
    )
    api.db.conn.commit()
    return oid


def test_scenarios_prepare_history_once_and_bulk_load_monitor_evidence(tmp_path, monkeypatch):
    api = API(tmp_path / "scenario-fast.sqlite3")
    _add_settled_with_monitor_evidence(api, "one")
    _add_settled_with_monitor_evidence(api, "two", "2026-08-09T10:01:00+00:00")

    calls = {"opportunity_rows": 0, "replay_opportunity_rows": 0, "bulk": 0}
    original_rows = api.db.opportunity_rows
    original_replay_rows = api.db.replay_opportunity_rows
    original_bulk = api.db.monitor_timing_runs_for_opportunities

    def counted_rows(*args, **kwargs):
        calls["opportunity_rows"] += 1
        return original_rows(*args, **kwargs)

    def counted_replay_rows(*args, **kwargs):
        calls["replay_opportunity_rows"] += 1
        return original_replay_rows(*args, **kwargs)

    def counted_bulk(*args, **kwargs):
        calls["bulk"] += 1
        return original_bulk(*args, **kwargs)

    monkeypatch.setattr(api.db, "opportunity_rows", counted_rows)
    monkeypatch.setattr(api.db, "replay_opportunity_rows", counted_replay_rows)
    monkeypatch.setattr(api.db, "monitor_timing_runs_for_opportunities", counted_bulk)
    monkeypatch.setattr(
        api.db, "monitor_timing_run_for_opportunity",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("N+1 monitor_timing lookup must not be used by optimized analytics_replay")),
    )

    result = api.analytics_replay({
        "exchange_balances": {"betfair": 250.0, "matchbook": 250.0},
        "minimum_deployed_roi_pct": 0,
        "minimum_profit": 0,
        "comparison_capitals": [500, 1000, 5000, 10000, 25000],
    })

    assert result["ok"] is True
    assert result["result"]["counts"]["taken"] >= 1
    assert calls == {"opportunity_rows": 0, "replay_opportunity_rows": 1, "bulk": 1}
    diag = result["scenario_diagnostics"]
    assert diag["history_preparations"] == 1
    assert diag["replay_variants"] == 9
    assert diag["monitor_runs_loaded"] == 2
    assert diag["observations_loaded"] == 6
    assert diag["scenario_total_ms"] >= 0


def test_prepared_replay_is_numerically_equivalent_to_legacy_path(tmp_path):
    api = API(tmp_path / "scenario-equivalence.sqlite3")
    _add_settled_with_monitor_evidence(api, "equiv")
    kwargs = dict(
        starting_capital=500.0,
        max_event_exposure_pct=100,
        min_profit=0,
        min_deployed_roi_pct=0,
        include_demo=False,
        exchange_balances={"betfair": 250.0, "matchbook": 250.0},
        require_monitor_evidence=True,
        monitor_stream="combined",
        minimum_quality_band="all",
        time_basis="detected_at",
    )
    legacy = replay_analysis(api.db, **kwargs)
    prepared = prepare_replay_history(
        api.db, include_demo=False, minimum_quality_band="all", time_basis="detected_at", require_monitor_evidence=True,
    )
    optimized = replay_analysis(api.db, **kwargs, prepared_history=prepared)
    assert optimized == legacy


def test_bulk_monitor_timing_loader_matches_single_lookup_semantics(tmp_path):
    api = API(tmp_path / "bulk-monitor_timing.sqlite3")
    oid = _add_settled_with_monitor_evidence(api, "bulk")
    single = api.db.monitor_timing_run_for_opportunity(oid, stream="pre_match")
    bulk = api.db.monitor_timing_runs_for_opportunities([oid])[(oid, "pre_match")]
    assert bulk == single


def test_frontend_scenarios_dedupes_requests_and_skips_intermediate_analytics_load():
    assert "scenarioReplayRequest098(payload)" in HTML
    assert "scenarioReplayInflightPromise&&scenarioReplayInflightKey===key" in HTML
    assert "ticket.generation!==scenarioReplayGeneration" in HTML
    assert "Preparing historical scenario…" in HTML
    assert "Loading historical scenario…" in HTML
    assert "showTab('analytics',true);showAnalyticsPane(name)" in HTML
    assert "function showTab(id,skipRouteLoad=false)" in HTML
