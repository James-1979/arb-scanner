from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API
from arbscanner.models import Leg
from arbscanner.replay import prepare_replay_history

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
API_SOURCE = (ROOT / "arbscanner" / "api.py").read_text()
REPLAY_SOURCE = (ROOT / "arbscanner" / "replay.py").read_text()
INSTALLER = (ROOT / "BUILD_AND_INSTALL.command").read_text()
NOTES = (ROOT / "RELEASE_NOTES.md").read_text()


def _legs():
    return [
        Leg("Matchbook", "Home", 2.2, 200, 2.0, event_id="mb-e", market_id="mb-m", selection_id="1"),
        Leg("Betfair delayed", "Away", 2.3, 200, 2.0, event_id="bf-e", market_id="bf-m", selection_id="2"),
    ]


def _settled(api: API, suffix: str, engine: str, *, racing: bool = False) -> int:
    legs = _legs()
    oid = api.db.add_opportunity(
        f"evt-{suffix}", f"Event {suffix}", "2026-08-14T20:00:00+00:00", "Win" if racing else "Match Odds",
        1.0, 1.0, [asdict(x) for x in legs], [], 0.99, f"sig-0951-{suffix}",
        sport="Greyhounds" if racing else "Football", section="racing" if racing else "sports",
        engine_instance_id=engine, engine_type="baseline_arb", engine_version="1.0.0", engine_config_version=1,
    )
    api.db.settle(oid, "Home")
    api.db.conn.commit()
    return oid


def test_v0951_release_identity():
    assert __version__ == "0.9.51"
    assert '<title>ArbScanner PoC 0.9.51</title>' in HTML
    assert 'EXPECTED_VERSION="0.9.51"' in INSTALLER
    assert "## 0.9.51 — Scenarios Routing & Engine Integrity Closure" in NOTES


def test_scenario_engine_provenance_filter_is_database_owned(tmp_path):
    api = API(tmp_path / "engine-filter.sqlite3")
    a = _settled(api, "a", "SPORTS_BASELINE_ARB_PRIMARY")
    _settled(api, "b", "SPORTS_SUPERBET_ARB_PRIMARY")
    rows = api.db.replay_opportunity_rows(engine_instance_id="SPORTS_BASELINE_ARB_PRIMARY")
    assert [int(x["id"]) for x in rows] == [a]
    assert rows[0]["engine_instance_id"] == "SPORTS_BASELINE_ARB_PRIMARY"


def test_racing_history_remains_a_distinct_scenario_stream(tmp_path):
    api = API(tmp_path / "racing-route.sqlite3")
    oid = _settled(api, "race", "GREYHOUNDS_BASELINE_ARB_PRIMARY", racing=True)
    prepared = prepare_replay_history(api.db, include_demo=False, require_monitor_evidence=False)
    row = next(x for x in prepared["rows"] if int(x["id"]) == oid)
    assert row["_row_stream"] == "racing"
    assert 'row_stream = str(row.get("_row_stream")' in REPLAY_SOURCE
    assert 'racing_execution_hedge_reserve_pct' in REPLAY_SOURCE
    assert 'racing_execution_max_slippage_pct' in REPLAY_SOURCE
    assert 'racing_execution_max_unhedged_exposure' in REPLAY_SOURCE


def test_analytics_replay_exposes_all_four_routed_stream_models(tmp_path):
    api = API(tmp_path / "stream-models.sqlite3")
    result = api.analytics_replay({"venue_balances": {"betfair": 250.0, "matchbook": 250.0}, "comparison_capitals": []})
    assert result["ok"] is True
    assert set(result["stream_comparison"]) == {"pre_match", "in_play", "racing", "combined"}
    assert result["scenario_diagnostics"]["replay_variants"] >= 4


def test_actual_performance_comparator_is_engine_and_routing_aware():
    assert 'actual_stream = source_stream if capital_source == "market_budget" and source_stream in {"pre_match", "in_play", "racing"} else "all"' in API_SOURCE
    assert 'actual_scope = "racing" if sport.strip().lower() == "greyhounds" else "all"' in API_SOURCE
    assert '"engine_instance_id": engine_instance_id, "basis": "actual"' in API_SOURCE
    assert 'engine_filter = str(data.get("engine_instance_id") or data.get("engine") or "all").strip()' in API_SOURCE


def test_scenario_frontend_has_one_route_owner_and_no_stale_loader_layers():
    assert HTML.count("async function loadReplay(){") == 1
    assert "__loadReplay0834" not in HTML
    assert "replayBounds=function" not in HTML
    assert "loadScenarioContext0951" in HTML
    assert "scenarioReplayRequest098(payload)" in HTML
    assert "ws.betfair||bf" not in HTML
    assert "ws.matchbook||mb" not in HTML
    assert "loadScenarioCapitalSources();loadDatabaseCompactionStatus" not in HTML


def test_scenario_period_engine_racing_and_live_isolation_controls_are_wired():
    for value in ("yesterday", "this_week", "this_month"):
        assert f"period==='{value}'" in HTML
    assert '<option>Greyhounds</option>' in HTML
    assert '<option value="racing">Racing</option>' in HTML
    assert 'id="scenarioOriginEngine0951"' in HTML
    assert "engine_instance_id:engineId" in HTML
    assert "rs('racing','replayRacing')" in HTML
    assert 'id="scenarioLiveNotice0951"' in HTML
    assert 'id="scenarioSimContent0951"' in HTML
    assert "if(dataContextMode!=='sim')return loadLiveScenarioEmpty()" in HTML
    assert "if(content)content.hidden=live" in HTML


def test_scenario_transactions_expose_engine_provenance_and_engine_model_handoff():
    assert '<th>Engine</th>' in HTML
    assert '<th>Model</th>' in HTML
    assert 'openScenarioEngineForOpportunity0951' in HTML
    assert 'scenarioEngineLabel0951(e.engine_instance_id)' in HTML
    assert 'engine_scenario_compare' in HTML
