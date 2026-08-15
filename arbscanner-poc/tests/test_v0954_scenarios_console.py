from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API
from arbscanner.engine import simulate_equal_return
from arbscanner.models import Leg, Scenario
from arbscanner.replay import replay_analysis

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
API_SOURCE = (ROOT / "arbscanner" / "api.py").read_text()
REPLAY_SOURCE = (ROOT / "arbscanner" / "replay.py").read_text()
INSTALLER = (ROOT / "BUILD_AND_INSTALL.command").read_text()
NOTES = (ROOT / "RELEASE_NOTES.md").read_text()


def _legs():
    return [
        Leg("Matchbook", "Home", 2.72, 420, 2.0, event_id="mb-e", market_id="mb-m", selection_id="4"),
        Leg("Betfair delayed", "Draw", 3.75, 265, 2.0, event_id="bf-e", market_id="bf-m", selection_id="2"),
        Leg("Betfair delayed", "Away", 3.05, 180, 2.0, event_id="bf-e", market_id="bf-m", selection_id="3"),
    ]


def _settled(api: API, suffix: str, *, sport: str, engine: str, with_monitor: bool = False) -> int:
    legs = _legs()
    detected = "2026-08-09T10:00:00+00:00"
    oid = api.db.add_opportunity(
        f"evt-{suffix}", f"Alpha {suffix} v Beta {suffix}", "2026-08-09T12:00:00+00:00", "Match Odds",
        2.0, 2.0, [asdict(x) for x in legs], [], 0.99, f"sig-v0954-{suffix}",
        sport=sport, section="racing" if sport == "Greyhounds" else "sports",
        engine_instance_id=engine, engine_type="baseline_arb", engine_version="1.0.0", engine_config_version=1,
    )
    api.db.conn.execute("UPDATE opportunities SET detected_at=? WHERE id=?", (detected, oid))
    api.db.settle(oid, "Home")
    api.db.conn.execute("UPDATE settlements SET settled_at=? WHERE opportunity_id=?", ("2026-08-09T15:00:00+00:00", oid))
    if with_monitor:
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
                rid, offset_ms=offset, elapsed_ms=offset + 10,
                observed_at=f"2026-08-09T10:00:00.{offset:03d}+00:00", fetch_latency_ms=10,
                deployed=sim["deployed"], expected_profit=sim["expected_profit"], expected_roi_pct=sim["expected_roi_pct"],
                executable_fraction=1.0, full_stake_available=True, still_profitable=True, still_executable=True,
                failure_reason=None, quotes=quotes, venues=[],
            )
        api.db.finish_monitor_timing_run(
            rid, finished_at="2026-08-09T10:00:01.100+00:00", status="COMPLETE", survived_through_ms=1000,
            first_failure_reason=None, reference_profit=sim["expected_profit"], reference_roi_pct=sim["expected_roi_pct"],
            reference_executable=True,
        )
    api.db.conn.commit()
    return oid


def test_v0954_release_identity():
    assert __version__ == "0.9.54"
    assert '<title>ArbScanner PoC 0.9.54</title>' in HTML
    assert 'EXPECTED_VERSION="0.9.54"' in INSTALLER
    assert "## 0.9.54 — Scenarios Console Closure" in NOTES


def test_scenarios_is_one_page_console_with_exact_horizons_and_primary_exposure():
    assert 'class="analytics-pane scenario-console0954"' in HTML
    for text in ("24 Hours", "48 Hours", "7 Days"):
        assert text in HTML
    for control in (
        "scenarioDate0954", "scenarioSportButtons0954", "scenarioStreamButtons0954", "scenarioVenueButtons0954",
        "scenarioQualityButtons0954", "scenarioStartingBalance0954", "scenarioHedge0954", "scenarioMinProfit0954",
        "scenarioMaxStake0954", "scenarioMinRoi0954", "scenarioEngineButtons0954",
    ):
        assert f'id="{control}"' in HTML
    assert '<span>Max Capital Exposure</span><strong id="scenarioExposure0954">' in HTML
    assert 'Balance / Capital Exposure Timeline' in HTML
    assert 'scenario-output0954' in HTML and 'max-height:31vh' in HTML
    assert 'scenarioEngineModel0951' not in HTML
    assert 'scenarioResultsRows' not in HTML
    assert 'cmpMonitorEnd' not in HTML


def test_scenario_date_is_start_date_and_horizon_runs_forward_bounded_by_now():
    assert '<span>Start Date ' in HTML
    assert 'requestedEnd=new Date(from.getTime()+Math.max(1,Number(scenarioHorizonHours0954||24))*3600000)' in HTML
    assert 'Math.min(requestedEnd.getTime(),now.getTime()+1000)' in HTML
    assert 'date.max=scenarioTodayValue0954()' in HTML


def test_scenario_payload_routes_multi_select_dimensions_and_local_risk_controls():
    for fragment in (
        'sports,streams,exchanges,engine_instance_ids:engines',
        'max_stake:maxStake',
        'hedge_reserve_pct:hedge',
        'comparison_capitals:[]',
        "capital_source:'custom'",
        "time_basis:'settled_at'",
    ):
        assert fragment in HTML
    assert 'scenarioReplayRequest098(payload)' in HTML
    assert HTML.count('async function loadReplay(){') == 1
    assert 'if(dataContextMode===\'live\')return loadLiveScenarioEmpty()' in HTML
    assert 'scenarioAllEngines0954=true' in HTML
    assert 'No stored Engine provenance' in HTML
    assert 'Legacy / Unverified' not in HTML


def test_database_multi_sport_and_multi_engine_filters_are_authoritative(tmp_path):
    api = API(tmp_path / "scenario-multifilter.sqlite3")
    football = _settled(api, "football", sport="Football", engine="SPORTS_BASELINE_ARB_PRIMARY")
    greyhounds = _settled(api, "racing", sport="Greyhounds", engine="GREYHOUNDS_BASELINE_ARB_PRIMARY")
    _settled(api, "tennis", sport="Tennis", engine="SPORTS_SUPERBET_ARB_PRIMARY")
    rows = api.db.replay_opportunity_rows(
        sports=["Football", "Greyhounds"],
        engine_instance_ids=["SPORTS_BASELINE_ARB_PRIMARY", "GREYHOUNDS_BASELINE_ARB_PRIMARY"],
    )
    assert [int(row["id"]) for row in rows] == [football, greyhounds]


def test_analytics_replay_preserves_multi_filters_in_result_contract(tmp_path):
    api = API(tmp_path / "scenario-contract.sqlite3")
    result = api.analytics_replay({
        "venue_balances": {"betfair": 250.0, "matchbook": 250.0},
        "comparison_capitals": [],
        "sports": ["Football", "Greyhounds"],
        "streams": ["pre_match", "racing"],
        "exchanges": ["betfair", "matchbook"],
        "engine_instance_ids": ["SPORTS_BASELINE_ARB_PRIMARY", "GREYHOUNDS_BASELINE_ARB_PRIMARY"],
        "max_stake": 12.5,
        "hedge_reserve_pct": 30,
    })
    assert result["ok"] is True
    assert result["sports"] == ["Football", "Greyhounds"]
    assert result["streams"] == ["pre_match", "racing"]
    assert result["exchanges"] == ["betfair", "matchbook"]
    assert result["engine_instance_ids"] == ["SPORTS_BASELINE_ARB_PRIMARY", "GREYHOUNDS_BASELINE_ARB_PRIMARY"]
    filters = result["result"]["filters"]
    assert filters["sports"] == ["Football", "Greyhounds"]
    assert filters["monitor_streams"] == ["pre_match", "racing"]
    assert filters["engine_instance_ids"] == ["SPORTS_BASELINE_ARB_PRIMARY", "GREYHOUNDS_BASELINE_ARB_PRIMARY"]
    assert filters["max_stake"] == 12.5
    assert filters["hedge_reserve_pct"] == 30.0


def test_scenario_max_stake_changes_economics_without_persisting_config(tmp_path):
    api = API(tmp_path / "scenario-max-stake.sqlite3")
    _settled(api, "cap", sport="Football", engine="SPORTS_BASELINE_ARB_PRIMARY", with_monitor=True)
    before = dict(api.db.get_setting("config", {}) or {})
    common = {
        "venue_balances": {"betfair": 250.0, "matchbook": 250.0},
        "minimum_deployed_roi_pct": 0,
        "minimum_profit": 0,
        "comparison_capitals": [],
        "streams": ["pre_match"],
        "hedge_reserve_pct": 0,
    }
    uncapped = api.analytics_replay({**common, "max_stake": 1000.0})["result"]
    capped = api.analytics_replay({**common, "max_stake": 5.0})["result"]
    assert uncapped["counts"]["taken"] == 1
    assert capped["counts"]["taken"] == 1
    assert capped["counts"].get("stake_capped") == 1
    assert capped["total_deployed"] < uncapped["total_deployed"]
    assert capped["realized_profit"] < uncapped["realized_profit"]
    assert dict(api.db.get_setting("config", {}) or {}) == before
    assert 'base = scale_simulation(base, factor, total_bankroll=equity_total())' in REPLAY_SOURCE
    assert 'scenario_reserve = min(100.0, max(0.0, float(hedge_reserve_pct)))' in REPLAY_SOURCE



def test_legacy_pooled_replay_path_accepts_new_filter_contract_without_name_errors(tmp_path):
    api = API(tmp_path / "scenario-pooled-compat.sqlite3")
    result = replay_analysis(
        api.db, 500.0, sports=["Football"], monitor_streams=["pre_match", "racing"],
        engine_instance_ids=["SPORTS_BASELINE_ARB_PRIMARY"], exchanges=["betfair", "matchbook"], max_stake=10.0,
    )
    assert result["filters"]["sports"] == ["Football"]
    assert result["filters"]["monitor_streams"] == ["pre_match", "racing"]
    assert result["filters"]["engine_instance_ids"] == ["SPORTS_BASELINE_ARB_PRIMARY"]
    assert result["filters"]["exchanges"] == ["betfair", "matchbook"]
    assert result["filters"]["max_stake"] == 10.0

def test_live_scenarios_remains_render_and_request_isolated():
    assert 'id="scenarioLiveNotice0951"' in HTML
    assert 'id="scenarioSimContent0951"' in HTML
    assert 'if(content)content.hidden=live' in HTML
    assert '#scenarioSimContent0951[hidden]{display:none!important}' in HTML
    assert "if(dataContextMode==='live')return loadLiveScenarioEmpty()" in HTML
    assert 'live_execution_allowed": False' in API_SOURCE
