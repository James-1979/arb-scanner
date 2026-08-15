from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from arbscanner import __version__
from arbscanner.db import DB
from arbscanner.engine import best_strategy_legs
from arbscanner.models import Leg
from arbscanner.strategy_engines import (
    DecisionIntent,
    EngineRegistry,
    EngineRuntime,
    MarketEvidence,
    effective_lifecycle,
    validate_intent,
)


def make_db(tmp_path: Path) -> DB:
    db = DB(tmp_path / "arbscanner.sqlite3")
    db.ensure_default_engines()
    return db


def candidates() -> dict[str, list[Leg]]:
    return {
        "Home": [
            Leg("Betfair delayed", "Home", 2.60, 120.0, 2.0, market_id="m", selection_id="h", provider_id="betfair", venue_id="betfair"),
            Leg("Matchbook", "Home", 2.55, 130.0, 2.0, market_id="m2", selection_id="h2", provider_id="matchbook", venue_id="matchbook"),
        ],
        "Draw": [
            Leg("Betfair delayed", "Draw", 3.80, 110.0, 2.0, market_id="m", selection_id="d", provider_id="betfair", venue_id="betfair"),
            Leg("Matchbook", "Draw", 3.70, 140.0, 2.0, market_id="m2", selection_id="d2", provider_id="matchbook", venue_id="matchbook"),
        ],
        "Away": [
            Leg("Betfair delayed", "Away", 3.20, 100.0, 2.0, market_id="m", selection_id="a", provider_id="betfair", venue_id="betfair"),
            Leg("Matchbook", "Away", 3.10, 150.0, 2.0, market_id="m2", selection_id="a2", provider_id="matchbook", venue_id="matchbook"),
        ],
    }


def evidence(section: str = "sports") -> MarketEvidence:
    market = SimpleNamespace(
        canonical_event_id="event-1", event_key="event-1", canonical_market_id="market-1",
        display_market="Match Odds", display_event="A v B", start_time="2026-08-13T18:00:00+00:00",
        section=section, sport="Football" if section == "sports" else "Greyhounds", competition="Test League",
        strategy="1x2", status="OPEN", in_play=False, canonical_market_type="Match Odds",
    )
    return MarketEvidence.from_candidates(market, candidates(), feed_generation="gen-7", observed_at="2026-08-13T15:00:00+00:00")


def test_version_is_0915():
    assert __version__ == "0.9.39"


def test_registry_has_reference_types_and_rejects_duplicates():
    registry = EngineRegistry()
    assert {x["engine_type"] for x in registry.types()} == {"SPORTS_BASELINE_ARB", "SPORTS_SUPERBET_ARB", "GREYHOUNDS_BASELINE_ARB", "SPORTS_DEPTH_ARB_REFERENCE", "NOOP_TEST_ENGINE"}
    with pytest.raises(ValueError):
        registry.register_type(type("Dup", (registry.create("NOOP_TEST_ENGINE", {}).__class__,), {"engine_type": "NOOP_TEST_ENGINE"}))


def test_engine_module_is_provider_blind():
    import arbscanner.strategy_engines as mod
    source = inspect.getsource(mod)
    assert "from .adapters" not in source
    assert "SecretStore" not in source
    assert "place_order" not in source.lower()
    assert "order-write" in source.lower()


def test_default_instances_are_seeded_without_changing_unrelated_settings(tmp_path):
    db = make_db(tmp_path)
    db.set_setting("sentinel_keep_me", {"x": 7})
    db.ensure_default_engines()
    rows = db.engine_instances()
    assert len(rows) == 5
    assert db.get_setting("sentinel_keep_me") == {"x": 7}
    assert next(x for x in rows if x["engine_instance_id"] == "SPORTS_BASELINE_ARB_PRIMARY")["effective_lifecycle"] == "SIM"
    assert next(x for x in rows if x["engine_instance_id"] == "SPORTS_DEPTH_ARB_REFERENCE")["effective_lifecycle"] == "DISABLED"


def test_requested_live_stays_live_authorised_while_execution_locked():
    assert effective_lifecycle("LIVE_APPROVED", live_execution_unlocked=False) == ("LIVE_APPROVED", "LIVE_EXECUTION_LOCKED")
    assert effective_lifecycle("MONITOR_TIMING", live_execution_unlocked=False) == ("DISABLED", "INVALID_REQUESTED_LIFECYCLE")


def test_immutable_configuration_history_creates_new_version(tmp_path):
    db = make_db(tmp_path)
    iid = "SPORTS_BASELINE_ARB_PRIMARY"
    first = db.engine_active_config(iid)
    changed = dict(first["config"])
    changed["minimum_edge"] = 0.75
    second = db.engine_create_config(iid, changed, activate=True)
    history = db.engine_config_history(iid)
    assert second["config_version"] == 2
    assert first["config_hash"] != second["config_hash"]
    assert {x["config_version"] for x in history} == {1, 2}
    assert next(x for x in history if x["config_version"] == 1)["config"]["minimum_edge"] == 0.0


def test_clone_copies_config_but_has_independent_identity(tmp_path):
    db = make_db(tmp_path)
    clone = db.engine_clone("SPORTS_BASELINE_ARB_PRIMARY", "FOOTBALL_TEST_A")
    assert clone["engine_instance_id"] == "FOOTBALL_TEST_A"
    assert clone["requested_lifecycle"] == "DISABLED"
    assert clone["active_config"]["config"] == db.engine_active_config("SPORTS_BASELINE_ARB_PRIMARY")["config"]


def test_router_separates_sports_and_racing(tmp_path):
    db = make_db(tmp_path)
    runtime = EngineRuntime(db)
    sports = runtime.router.route(db.engine_instances(), evidence("sports"))
    racing = runtime.router.route(db.engine_instances(), evidence("racing"))
    assert {x["engine_instance_id"] for x in sports} == {"SPORTS_BASELINE_ARB_PRIMARY"}
    assert {x["engine_instance_id"] for x in racing} == {"GREYHOUNDS_BASELINE_ARB_PRIMARY"}


def test_legacy_engine_selection_matches_established_strategy(tmp_path):
    db = make_db(tmp_path)
    runtime = EngineRuntime(db)
    ev = evidence()
    result = runtime.evaluate_legacy(ev, minimum_liquidity=2.0, require_cross_exchange=True, reference_bankroll=1000.0)
    expected = best_strategy_legs(candidates(), 2.0, require_cross_exchange=True)
    assert result is not None
    assert [(x.exchange, x.selection, x.odds) for x in result.selected_legs] == [(x.exchange, x.selection, x.odds) for x in expected]


def test_multiple_engines_can_consume_identical_snapshot(tmp_path):
    db = make_db(tmp_path)
    db.engine_set_lifecycle("SPORTS_DEPTH_ARB_REFERENCE", "SIM")
    runtime = EngineRuntime(db)
    results = runtime.evaluate(evidence())
    assert {x.context.engine_type for x in results} == {"SPORTS_BASELINE_ARB", "SPORTS_DEPTH_ARB_REFERENCE"}
    assert len({x.context.market_snapshot_id for x in results}) == 1


def test_noop_engine_can_be_exercised_in_research_without_operational_enable(tmp_path):
    db = make_db(tmp_path)
    runtime = EngineRuntime(db)
    result = runtime.evaluate(evidence(), instance_ids=["NOOP_FRAMEWORK_TEST"], research_mode=True, persist=False)
    assert len(result) == 1
    assert result[0].decision is None
    assert db.engine_instance("NOOP_FRAMEWORK_TEST")["requested_lifecycle"] == "DISABLED"


def test_config_provenance_is_frozen_per_evaluation(tmp_path):
    db = make_db(tmp_path)
    runtime = EngineRuntime(db)
    first = runtime.evaluate(evidence(), instance_ids=["SPORTS_BASELINE_ARB_PRIMARY"], persist=False)[0]
    cfg = dict(db.engine_active_config("SPORTS_BASELINE_ARB_PRIMARY")["config"])
    cfg["minimum_edge"] = 99.0
    db.engine_create_config("SPORTS_BASELINE_ARB_PRIMARY", cfg, activate=True)
    second = runtime.evaluate(evidence(), instance_ids=["SPORTS_BASELINE_ARB_PRIMARY"], persist=False)[0]
    assert first.context.config_version == 1
    assert second.context.config_version == 2
    assert first.context.config_hash != second.context.config_hash


def test_central_validation_rejects_generation_mismatch_and_expiry(tmp_path):
    db = make_db(tmp_path)
    runtime = EngineRuntime(db)
    result = runtime.evaluate(evidence(), instance_ids=["SPORTS_BASELINE_ARB_PRIMARY"], persist=False, evaluation_timestamp="2026-08-13T15:00:00+00:00")[0]
    assert result.decision is not None
    mismatch = validate_intent(result.decision, current_feed_generation="gen-other", now="2026-08-13T15:00:00+00:00")
    assert "FEED_GENERATION_MISMATCH" in mismatch["reasons"]
    expired = validate_intent(result.decision, current_feed_generation="gen-7", now="2026-08-13T16:00:00+00:00")
    assert "DECISION_EXPIRED" in expired["reasons"]


def test_equivalent_economic_intents_have_same_dedupe_key(tmp_path):
    db = make_db(tmp_path)
    db.engine_set_lifecycle("SPORTS_DEPTH_ARB_REFERENCE", "SIM")
    results = EngineRuntime(db).evaluate(evidence(), persist=False)
    decisions = [x.decision for x in results if x.decision]
    assert len(decisions) >= 2
    assert len({x.economic_intent_key for x in decisions}) == 1
    assert len({x.decision_id for x in decisions}) == len(decisions)


def test_sim_economics_are_partitioned_by_engine_instance(tmp_path):
    db = make_db(tmp_path)
    db.engine_set_lifecycle("SPORTS_DEPTH_ARB_REFERENCE", "SIM")
    EngineRuntime(db).evaluate(evidence())
    legacy = db.engine_performance("SPORTS_BASELINE_ARB_PRIMARY")["sim"]
    depth = db.engine_performance("SPORTS_DEPTH_ARB_REFERENCE")["sim"]
    assert legacy["decisions"] == 1
    assert depth["decisions"] == 1
    assert legacy["deployed"] > 0 and depth["deployed"] > 0


def test_third_engine_lifecycle_is_rejected(tmp_path):
    db = make_db(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        db.engine_set_lifecycle("SPORTS_BASELINE_ARB_PRIMARY", "MONITOR_TIMING")
    row = db.engine_instance("SPORTS_BASELINE_ARB_PRIMARY")
    assert row["requested_lifecycle"] == "SIM"
    assert row["effective_lifecycle"] == "SIM"


def test_engine_error_is_isolated_and_recorded(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    runtime = EngineRuntime(db)
    original = runtime.registry.create
    def bad_create(engine_type, config):
        engine = original(engine_type, config)
        if engine_type == "SPORTS_BASELINE_ARB":
            def boom(_context):
                raise RuntimeError("isolated boom")
            engine.evaluate = boom
        return engine
    monkeypatch.setattr(runtime.registry, "create", bad_create)
    result = runtime.evaluate(evidence())
    assert result[0].error and "isolated boom" in result[0].error
    row = db.conn.execute("SELECT * FROM engine_errors").fetchone()
    assert row and row["engine_instance_id"] == "SPORTS_BASELINE_ARB_PRIMARY"


def test_engine_ui_navigation_and_pages_are_present():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    sports = html[html.index('aria-label="Sports navigation"'):html.index('</div>', html.index('aria-label="Sports navigation"'))]
    racing = html[html.index('aria-label="Racing navigation"'):html.index('</div>', html.index('aria-label="Racing navigation"'))]
    assert [sports.index(x) for x in [">Overview<", ">Engines<", ">Monitor<", ">Results<", ">Config<"]] == sorted([sports.index(x) for x in [">Overview<", ">Engines<", ">Monitor<", ">Results<", ">Config<"]])
    assert [racing.index(x) for x in [">Overview<", ">Engines<", ">Monitor<", ">Results<", ">Config<"]] == sorted([racing.index(x) for x in [">Overview<", ">Engines<", ">Monitor<", ">Results<", ">Config<"]])
    assert 'id="sports-engines"' in html and 'id="racing-engines"' in html
    assert 'data-tab="sports-execution" data-nav-child="sports"' not in sports
    assert 'data-tab="racing-execution" data-nav-child="racing"' not in racing


def test_markets_remain_evidence_oriented_in_engine_ui():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert "best strategy" not in html[html.index('id="sports-engines"'):html.index('id="sports-config"')].lower()
    assert "provider credentials" in html[html.index('id="sports-engines"'):html.index('id="sports-config"')].lower()


def historical_row(observed_at: str = "2026-08-13T15:00:00+00:00") -> dict:
    from dataclasses import asdict
    legs = [asdict(leg) for selection in candidates().values() for leg in selection]
    return {
        "id": 1,
        "event_key": "event-1",
        "event_id": "event-1",
        "event_name": "A v B",
        "market_id": "market-1",
        "market_name": "Match Odds",
        "market_type": "Match Odds",
        "sport": "Football",
        "competition": "Test League",
        "section": "sports",
        "strategy": "1x2",
        "event_status": "OPEN",
        "in_play": 0,
        "book_revision": "gen-7",
        "observed_at": observed_at,
        "detected_at": observed_at,
        "legs_json": json.dumps(legs),
    }


def test_route_update_is_external_to_engine_implementation(tmp_path):
    from arbscanner.api import API
    api = API(tmp_path / "arbscanner.sqlite3")
    result = api.engine_set_route({
        "engine_instance_id": "SPORTS_DEPTH_ARB_REFERENCE",
        "section": "sports",
        "sport": "Football",
        "competition": "Premier League",
        "market_type": "Match Odds",
    })
    assert result["ok"] is True
    row = api.db.engine_instance("SPORTS_DEPTH_ARB_REFERENCE")
    assert (row["section"], row["sport"], row["competition"], row["market_type"]) == (
        "sports", "Football", "Premier League", "Match Odds"
    )
    assert "editEngineRoute0914" in (Path(__file__).parents[1] / "frontend" / "index.html").read_text()


def test_scenario_compare_runs_disabled_engine_as_research_and_persists_provenance(tmp_path, monkeypatch):
    from arbscanner.api import API
    api = API(tmp_path / "arbscanner.sqlite3")
    row = historical_row()
    monkeypatch.setattr(api.db, "opportunity_by_id", lambda *_a, **_k: dict(row))
    result = api.engine_scenario_compare({
        "opportunity_id": 42,
        "engine_instance_ids": ["SPORTS_DEPTH_ARB_REFERENCE"],
        "scenario_id": "PRICE_DISAPPEARS",
        "scenario_version": 3,
        "simulation_level": "DECISION_SIM",
    })
    assert result["ok"] is True
    assert len(result["rows"]) == 1
    run = result["rows"][0]
    assert run["engine_instance_id"] == "SPORTS_DEPTH_ARB_REFERENCE"
    assert run["config_version"] == 1
    saved = api.db.conn.execute("SELECT * FROM engine_scenario_runs WHERE run_id=?", (run["run_id"],)).fetchone()
    assert saved is not None
    assert saved["scenario_id"] == "PRICE_DISAPPEARS"
    assert saved["scenario_version"] == 3
    assert saved["market_snapshot_id"] == result["market_snapshot_id"]
    assert api.db.engine_instance("SPORTS_DEPTH_ARB_REFERENCE")["requested_lifecycle"] == "DISABLED"


def test_replay_compare_passes_only_current_historical_snapshot_to_engine(tmp_path, monkeypatch):
    from arbscanner.api import API
    api = API(tmp_path / "arbscanner.sqlite3")
    rows = [
        historical_row("2026-08-13T15:00:00+00:00"),
        historical_row("2026-08-13T15:01:00+00:00"),
    ]
    monkeypatch.setattr(api.analytics_store, "detailed_history", lambda *_a, **_k: {
        "ok": True,
        "from_utc": "2026-08-13T15:00:00+00:00",
        "to_utc": "2026-08-13T15:02:00+00:00",
        "rows": rows,
        "archive_hours": ["2026-08-13T15:00:00+00:00"],
        "sqlite_hours": [],
    })
    seen = []
    original = api.scanner.engine_runtime.evaluate
    def capture(ev, **kwargs):
        seen.append((ev.observed_at, kwargs.get("evaluation_timestamp")))
        return original(ev, **kwargs)
    monkeypatch.setattr(api.scanner.engine_runtime, "evaluate", capture)
    result = api.engine_replay_compare({
        "from_utc": "2026-08-13T15:00:00+00:00",
        "to_utc": "2026-08-13T15:02:00+00:00",
        "engine_instance_ids": ["SPORTS_BASELINE_ARB_PRIMARY"],
    })
    assert result["ok"] is True
    assert result["no_lookahead"] is True
    assert result["evaluated_market_rows"] == 2
    assert seen == [
        ("2026-08-13T15:00:00+00:00", "2026-08-13T15:00:00+00:00"),
        ("2026-08-13T15:01:00+00:00", "2026-08-13T15:01:00+00:00"),
    ]


def test_engine_runtime_fanout_obeys_platform_resource_cap(tmp_path):
    db = make_db(tmp_path)
    for idx in range(15):
        iid = f"SPORTS_NOOP_{idx:02d}"
        db.engine_clone("NOOP_FRAMEWORK_TEST", iid, requested_lifecycle="SIM")
        db.engine_set_route(iid, section="sports", sport="all", competition="all", market_type="all")
    db.set_setting("config", {"engine_max_concurrent_runtimes": 7})
    results = EngineRuntime(db).evaluate(evidence(), research_mode=False, persist=False)
    assert len(results) == 7


def test_registry_exposes_and_enforces_configuration_schema(tmp_path):
    db = make_db(tmp_path)
    registry = EngineRegistry()
    legacy = next(x for x in registry.types() if x["engine_type"] == "SPORTS_BASELINE_ARB")
    assert "minimum_edge" in legacy["config_schema"]
    with pytest.raises(ValueError):
        db.engine_create_config("SPORTS_BASELINE_ARB_PRIMARY", {"made_up_strategy_knob": 7}, activate=True)
    with pytest.raises(ValueError):
        db.engine_create_config("SPORTS_BASELINE_ARB_PRIMARY", {"maximum_slippage": -1}, activate=True)


def test_central_validation_rejects_second_equivalent_economic_intent(tmp_path):
    db = make_db(tmp_path)
    db.engine_set_lifecycle("SPORTS_DEPTH_ARB_REFERENCE", "SIM")
    decisions = [r.decision for r in EngineRuntime(db).evaluate(evidence(), persist=False) if r.decision]
    assert len(decisions) >= 2 and decisions[0].economic_intent_key == decisions[1].economic_intent_key
    seen = set()
    first = validate_intent(decisions[0], current_feed_generation="gen-7", now="2026-08-13T15:00:00+00:00", seen_economic_intents=seen)
    second = validate_intent(decisions[1], current_feed_generation="gen-7", now="2026-08-13T15:00:00+00:00", seen_economic_intents=seen)
    assert first["ok"] is True
    assert "DUPLICATE_ECONOMIC_INTENT" in second["reasons"]


def test_engine_disable_during_evaluation_finishes_frozen_context_then_stops_next(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    runtime = EngineRuntime(db)
    original_create = runtime.registry.create
    def create(engine_type, config):
        engine = original_create(engine_type, config)
        if engine_type == "SPORTS_BASELINE_ARB":
            original_evaluate = engine.evaluate
            def evaluate(context):
                db.engine_set_lifecycle(context.engine_instance_id, "DISABLED")
                return original_evaluate(context)
            engine.evaluate = evaluate
        return engine
    monkeypatch.setattr(runtime.registry, "create", create)
    first = runtime.evaluate(evidence(), instance_ids=["SPORTS_BASELINE_ARB_PRIMARY"], persist=False)
    assert len(first) == 1 and first[0].context.effective_lifecycle == "SIM"
    assert runtime.evaluate(evidence(), instance_ids=["SPORTS_BASELINE_ARB_PRIMARY"], persist=False) == []


def test_config_activation_during_evaluation_does_not_mutate_started_context(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    runtime = EngineRuntime(db)
    original_create = runtime.registry.create
    changed = {"done": False}
    def create(engine_type, config):
        engine = original_create(engine_type, config)
        if engine_type == "SPORTS_BASELINE_ARB" and not changed["done"]:
            original_evaluate = engine.evaluate
            def evaluate(context):
                cfg = dict(db.engine_active_config(context.engine_instance_id)["config"])
                cfg["minimum_edge"] = 0.25
                db.engine_create_config(context.engine_instance_id, cfg, activate=True)
                changed["done"] = True
                return original_evaluate(context)
            engine.evaluate = evaluate
        return engine
    monkeypatch.setattr(runtime.registry, "create", create)
    # Both economic modes can be independently enabled. The mode provider may
    # switch while work is in flight, but the started evaluation keeps its
    # frozen mode context.
    db.engine_set_mode_enablement("SPORTS_BASELINE_ARB_PRIMARY", "live", True)
    first = runtime.evaluate(evidence(), instance_ids=["SPORTS_BASELINE_ARB_PRIMARY"], persist=False)[0]
    second = runtime.evaluate(evidence(), instance_ids=["SPORTS_BASELINE_ARB_PRIMARY"], persist=False)[0]
    assert first.context.config_version == 1
    assert second.context.config_version == 2
    assert first.context.config_hash != second.context.config_hash


def test_mode_switch_during_evaluation_uses_frozen_mode_for_started_work(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    state = {"mode": "sim", "switched": False}
    runtime = EngineRuntime(db, mode_provider=lambda: state["mode"])
    original_create = runtime.registry.create
    def create(engine_type, config):
        engine = original_create(engine_type, config)
        if engine_type == "SPORTS_BASELINE_ARB" and not state["switched"]:
            original_evaluate = engine.evaluate
            def evaluate(context):
                state["mode"] = "live"
                state["switched"] = True
                return original_evaluate(context)
            engine.evaluate = evaluate
        return engine
    monkeypatch.setattr(runtime.registry, "create", create)
    # Both economic modes can be independently enabled. The mode provider may
    # switch while work is in flight, but the started evaluation keeps its
    # frozen mode context.
    db.engine_set_mode_enablement("SPORTS_BASELINE_ARB_PRIMARY", "live", True)
    first = runtime.evaluate(evidence(), instance_ids=["SPORTS_BASELINE_ARB_PRIMARY"], persist=False)[0]
    second = runtime.evaluate(evidence(), instance_ids=["SPORTS_BASELINE_ARB_PRIMARY"], persist=False)[0]
    assert first.context.mode == "sim"
    assert second.context.mode == "live"
