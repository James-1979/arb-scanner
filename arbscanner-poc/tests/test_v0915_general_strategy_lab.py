from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from arbscanner.db import DB
from arbscanner.models import Leg
from arbscanner.strategy_engines import DecisionIntent, EngineRuntime, MarketEvidence, validate_intent


def make_db(tmp_path: Path) -> DB:
    db = DB(tmp_path / "arbscanner.sqlite3")
    db.ensure_default_engines()
    return db


def evidence(section="sports"):
    c = {
        "Home": [Leg("Betfair", "Home", 2.6, 100, 2, market_id="m1", selection_id="h1", provider_id="betfair", venue_id="betfair")],
        "Draw": [Leg("Matchbook", "Draw", 3.8, 100, 2, market_id="m2", selection_id="d2", provider_id="matchbook", venue_id="matchbook")],
        "Away": [Leg("Betfair", "Away", 3.2, 100, 2, market_id="m1", selection_id="a1", provider_id="betfair", venue_id="betfair")],
    }
    market = SimpleNamespace(canonical_event_id="e1", event_key="e1", canonical_market_id="m", display_market="Match Odds",
        display_event="A v B", start_time="2026-08-13T19:00:00+00:00", section=section,
        sport="Greyhounds" if section=="racing" else "Football", competition="Test", strategy="1x2", status="OPEN",
        in_play=False, canonical_market_type="Match Odds")
    return MarketEvidence.from_candidates(market, c, feed_generation="g1", observed_at="2026-08-13T17:00:00+00:00")


def test_canonical_real_strategy_names_and_grades(tmp_path):
    db = make_db(tmp_path)
    rows = {x["engine_instance_id"]: x for x in db.engine_instances()}
    assert rows["SPORTS_BASELINE_ARB_PRIMARY"]["engine_type"] == "SPORTS_BASELINE_ARB"
    assert rows["SPORTS_BASELINE_ARB_PRIMARY"]["engine_grade"] == "STANDARD"
    assert rows["SPORTS_SUPERBET_ARB_PRIMARY"]["engine_type"] == "SPORTS_SUPERBET_ARB"
    assert rows["SPORTS_SUPERBET_ARB_PRIMARY"]["engine_grade"] == "ADVANCED"
    assert rows["GREYHOUNDS_BASELINE_ARB_PRIMARY"]["engine_type"] == "GREYHOUNDS_BASELINE_ARB"
    assert rows["GREYHOUNDS_BASELINE_ARB_PRIMARY"]["engine_grade"] == "STANDARD"


def test_greyhounds_ui_remains_first_class():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert 'id="racing"' in html
    assert 'id="racing-monitor"' in html
    assert 'id="racing-engines"' in html
    assert "Greyhound" in html
    assert "Greyhounds Baseline ARB" in html or "GREYHOUNDS_BASELINE_ARB" in html


def test_superbet_global_controls_retired_and_engine_ui_remains():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert 'id="superbetEnabled"' not in html
    assert "superbet_enabled:" not in html
    assert "Sports SuperBet ARB" in html or "SPORTS_SUPERBET_ARB" in html
    assert "Strategy settings moved to Engines" in html


def test_legacy_superbet_globals_import_then_retire(tmp_path):
    db = DB(tmp_path / "arbscanner.sqlite3")
    cfg = dict(db.get_setting("config", {}) or {})
    cfg.update({"superbet_enabled": True, "superbet_max_tranches": 7, "superbet_min_net_edge": 0.55, "sentinel": 42})
    db.set_setting("config", cfg)
    db.ensure_default_engines()
    row = db.engine_instance("SPORTS_SUPERBET_ARB_PRIMARY")
    assert row["requested_lifecycle"] == "SIM"
    assert row["active_config"]["config"]["max_tranches"] == 7
    assert row["active_config"]["config"]["min_net_edge"] == pytest.approx(0.55)
    after = db.get_setting("config", {})
    assert "superbet_enabled" not in after and "superbet_max_tranches" not in after
    assert after["sentinel"] == 42



def test_legacy_superbet_unlimited_tranches_preserved_in_engine_migration(tmp_path):
    db = DB(tmp_path / "arbscanner.sqlite3")
    cfg = dict(db.get_setting("config", {}) or {})
    cfg.update({"superbet_enabled": True, "superbet_max_tranches": "unlimited"})
    db.set_setting("config", cfg)
    db.ensure_default_engines()
    engine = db.engine_instance("SPORTS_SUPERBET_ARB_PRIMARY")
    assert engine["active_config"]["config"]["max_tranches"] == "unlimited"
    assert engine["requested_lifecycle"] == "SIM"
    assert "superbet_max_tranches" not in db.get_setting("config", {})

def test_old_0914_engine_identities_converge_without_duplicates(tmp_path):
    path = tmp_path / "arbscanner.sqlite3"
    db = DB(path); db.ensure_default_engines()
    # Simulate the identities that a 0.9.14 installation stored.
    with db.lock:
        db.conn.execute("UPDATE engine_instances SET engine_instance_id='SPORTS_LEGACY_SIMPLE_PRIMARY',engine_type='LEGACY_SIMPLE_ARB' WHERE engine_instance_id='SPORTS_BASELINE_ARB_PRIMARY'")
        db.conn.execute("UPDATE engine_configs SET engine_instance_id='SPORTS_LEGACY_SIMPLE_PRIMARY' WHERE engine_instance_id='SPORTS_BASELINE_ARB_PRIMARY'")
        db.conn.execute("UPDATE engine_instances SET engine_instance_id='RACING_LEGACY_SIMPLE_PRIMARY',engine_type='LEGACY_SIMPLE_ARB' WHERE engine_instance_id='GREYHOUNDS_BASELINE_ARB_PRIMARY'")
        db.conn.execute("UPDATE engine_configs SET engine_instance_id='RACING_LEGACY_SIMPLE_PRIMARY' WHERE engine_instance_id='GREYHOUNDS_BASELINE_ARB_PRIMARY'")
        db.conn.execute("UPDATE engine_instances SET engine_instance_id='SUPERBET_ARB_PRIMARY',engine_type='SUPERBET_ARB' WHERE engine_instance_id='SPORTS_SUPERBET_ARB_PRIMARY'")
        db.conn.execute("UPDATE engine_configs SET engine_instance_id='SUPERBET_ARB_PRIMARY' WHERE engine_instance_id='SPORTS_SUPERBET_ARB_PRIMARY'")
        db.conn.commit()
    db.conn.close()
    upgraded = DB(path); upgraded.ensure_default_engines()
    ids = [x["engine_instance_id"] for x in upgraded.engine_instances()]
    assert ids.count("SPORTS_BASELINE_ARB_PRIMARY") == 1
    assert ids.count("GREYHOUNDS_BASELINE_ARB_PRIMARY") == 1
    assert ids.count("SPORTS_SUPERBET_ARB_PRIMARY") == 1
    assert "SPORTS_LEGACY_SIMPLE_PRIMARY" not in ids
    assert "RACING_LEGACY_SIMPLE_PRIMARY" not in ids
    assert "SUPERBET_ARB_PRIMARY" not in ids


def test_grade_is_independent_from_lifecycle(tmp_path):
    db = make_db(tmp_path)
    row = db.engine_set_grade("SPORTS_BASELINE_ARB_PRIMARY", "EXTREME")
    assert row["engine_grade"] == "EXTREME"
    assert row["requested_lifecycle"] == "SIM"
    db.engine_set_lifecycle("SPORTS_BASELINE_ARB_PRIMARY", "DISABLED")
    assert db.engine_instance("SPORTS_BASELINE_ARB_PRIMARY")["engine_grade"] == "EXTREME"


def test_non_arb_intent_does_not_require_guaranteed_profit():
    base = DecisionIntent(
        decision_id="d", economic_intent_key="k", intent_type="OPEN_POSITION", engine_instance_id="OPEN_TEST",
        engine_type="OPEN_TEST", engine_version="1", engine_grade="RESEARCH", capabilities=("OPEN_POSITION",),
        config_version=1, config_hash="h", market_snapshot_id="s", feed_generation="g", created_at="2026-08-13T17:00:00+00:00",
        expires_at="2026-08-13T17:01:00+00:00", section="sports", sport="Football", event="A v B", market="Match Odds",
        legs=(), expected_edge=5.0, expected_profit=1.5, requested_capital=10.0, requested_stake=10.0,
        minimum_profit=None, maximum_slippage=1.0, expected_commission=0.0, expected_fees=0.0, mode="sim", reason_codes=(),
        strategy_metrics={"model_probability": 0.55, "market_probability": 0.5, "maximum_loss": 10.0},
    )
    # Common central safety still requires at least one leg.
    assert "ARBITRAGE_MINIMUM_PROFIT_REQUIRED" not in validate_intent(base, current_feed_generation="g", now="2026-08-13T17:00:10+00:00")["reasons"]
    arb = replace(base, intent_type="ARBITRAGE")
    assert "ARBITRAGE_MINIMUM_PROFIT_REQUIRED" in validate_intent(arb, current_feed_generation="g", now="2026-08-13T17:00:10+00:00")["reasons"]


def test_experiment_clone_is_research_and_source_config_immutable(tmp_path):
    db = make_db(tmp_path)
    source_before = db.engine_active_config("SPORTS_BASELINE_ARB_PRIMARY")
    exp = db.engine_create_experiment("SPORTS_BASELINE_ARB_PRIMARY", "SPORTS_BASELINE_ARB_TEST_A", config_overrides={"minimum_edge": 0.77})
    clone = db.engine_instance(exp["engine_instance_id"])
    assert clone["engine_grade"] == "RESEARCH"
    assert clone["requested_lifecycle"] == "EXPERIMENTAL"
    assert clone["active_config"]["config"]["minimum_edge"] == pytest.approx(0.77)
    assert db.engine_active_config("SPORTS_BASELINE_ARB_PRIMARY")["config_hash"] == source_before["config_hash"]


def test_scaled_entry_resolution_is_capability_driven(tmp_path):
    db = make_db(tmp_path)
    db.engine_set_lifecycle("SPORTS_SUPERBET_ARB_PRIMARY", "SIM")
    cfg = EngineRuntime(db).scaled_entry_execution_config(section="sports")
    assert cfg["enabled"] is True
    assert cfg["capability"] == "SCALED_ENTRY"
    source = (Path(__file__).parents[1] / "arbscanner" / "scanner.py").read_text()
    assert "SPORTS_SUPERBET_ARB" not in source
    assert ".superbet_execution_config(" not in source


def test_engine_lab_research_backend_remains_available_after_ui_moves_to_scenarios():
    # 0.9.36 deliberately removes Experiments/Compare from the Engines product UI.
    # The 0.9.36 research APIs remain compatible for stored evidence and Scenario tooling.
    source = (Path(__file__).parents[1] / "arbscanner" / "api.py").read_text()
    for text in ("def engine_create_experiment", "def engine_create_sweep", "def engine_replay_compare"):
        assert text in source
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert "Model strategy engines" in html
    assert "modelling and comparison belong in Scenarios" in html


def test_parameter_sweep_is_bounded_and_never_partially_creates_over_limit(tmp_path):
    from arbscanner.api import API
    api = API(tmp_path / "arbscanner.sqlite3")
    cfg = dict(api.db.get_setting("config", {}) or {})
    cfg["engine_experiment_variant_limit"] = 4
    api.db.set_setting("config", cfg)
    too_many = api.engine_create_sweep({
        "source_engine_instance_id": "SPORTS_BASELINE_ARB_PRIMARY",
        "prefix": "SPORTS_BASELINE_ARB_SWEEP",
        "grid": {"minimum_edge": [0.1, 0.2, 0.3], "maximum_slippage": [0.5, 1.0]},
    })
    assert too_many["ok"] is False
    assert too_many["status"] == "TOO_MANY_VARIANTS"
    assert too_many["requested_variants"] == 6
    assert api.db.engine_experiments() == []

    ok = api.engine_create_sweep({
        "source_engine_instance_id": "SPORTS_BASELINE_ARB_PRIMARY",
        "prefix": "SPORTS_BASELINE_ARB_SWEEP",
        "grid": {"minimum_edge": [0.1, 0.2], "maximum_slippage": [0.5, 1.0]},
    })
    assert ok["ok"] is True and len(ok["rows"]) == 4
    rows = api.db.engine_experiments()
    assert len(rows) == 4
    assert {r["engine_grade"] for r in rows} == {"RESEARCH"}


def test_replay_compare_uses_identical_evidence_cohort_and_reproducible_run_ids(tmp_path, monkeypatch):
    from dataclasses import asdict
    from arbscanner.api import API
    api = API(tmp_path / "arbscanner.sqlite3")
    api.db.engine_set_lifecycle("SPORTS_SUPERBET_ARB_PRIMARY", "SIM")
    legs = [asdict(leg) for selection in evidence().leg_candidates().values() for leg in selection]
    def hist_row(idx, observed):
        return {
            "id": idx, "event_key": "e1", "event_id": "e1", "event_name": "A v B",
            "market_id": "m", "market_name": "Match Odds", "market_type": "Match Odds",
            "sport": "Football", "competition": "Test", "section": "sports", "strategy": "1x2",
            "event_status": "OPEN", "in_play": 0, "book_revision": "g1", "observed_at": observed,
            "detected_at": observed, "legs_json": json.dumps(legs),
        }
    history_rows = [
        hist_row(1, "2026-08-13T17:00:00+00:00"),
        hist_row(2, "2026-08-13T17:01:00+00:00"),
    ]
    monkeypatch.setattr(api.analytics_store, "detailed_history", lambda *_a, **_k: {
        "ok": True, "from_utc": "2026-08-13T17:00:00+00:00", "to_utc": "2026-08-13T17:02:00+00:00",
        "rows": [dict(x) for x in history_rows], "archive_hours": ["2026-08-13T17:00:00+00:00"], "sqlite_hours": [],
    })
    payload = {
        "from_utc": "2026-08-13T17:00:00+00:00", "to_utc": "2026-08-13T17:02:00+00:00",
        "engine_instance_ids": ["SPORTS_BASELINE_ARB_PRIMARY", "SPORTS_SUPERBET_ARB_PRIMARY"],
    }
    first = api.engine_replay_compare(payload)
    second = api.engine_replay_compare(payload)
    assert first["ok"] is True and first["no_lookahead"] is True
    assert first["evaluated_market_rows"] == 2
    assert first["evidence_cohort_hash"] == second["evidence_cohort_hash"]
    assert {r["engine_instance_id"] for r in first["rows"]} == {
        "SPORTS_BASELINE_ARB_PRIMARY", "SPORTS_SUPERBET_ARB_PRIMARY"
    }
    assert {r["engine_instance_id"]: r["experiment_run_id"] for r in first["rows"]} == {
        r["engine_instance_id"]: r["experiment_run_id"] for r in second["rows"]
    }
    assert all(r["config_hash"] for r in first["rows"])


def test_0914_to_0915_upgrade_preserves_archive_pilot_and_pruning_state(tmp_path):
    from arbscanner.archive import default_archive_root, save_runtime_gate_report
    path = tmp_path / "arbscanner.sqlite3"
    db = DB(path)
    db.ensure_default_engines()
    cfg = dict(db.get_setting("config", {}) or {})
    cfg.update({
        "matched_market_archive_enabled": True,
        "matched_market_archive_runtime_gate_required": True,
        "matched_market_archive_required_before_prune": False,
        "sentinel_0915_upgrade": "keep",
    })
    db.set_setting("config", cfg)
    db.set_setting("archive_runtime_pause_until", "2026-08-13T19:00:00+00:00")
    root = default_archive_root(path)
    root.mkdir(parents=True, exist_ok=True)
    sentinel = root / "upgrade-sentinel.bin"
    sentinel.write_bytes(b"archive-must-survive")
    save_runtime_gate_report(root, {
        "ok": True, "status": "PASS", "gate_protocol_version": 1, "archive_schema_version": 1,
        "hour_utc": "2026-08-13T13:00:00+00:00",
    })
    # Simulate released 0.9.36 engine identities immediately before 0.9.36 migration.
    with db.lock:
        db.conn.execute("UPDATE engine_instances SET engine_instance_id='SPORTS_LEGACY_SIMPLE_PRIMARY',engine_type='LEGACY_SIMPLE_ARB' WHERE engine_instance_id='SPORTS_BASELINE_ARB_PRIMARY'")
        db.conn.execute("UPDATE engine_configs SET engine_instance_id='SPORTS_LEGACY_SIMPLE_PRIMARY' WHERE engine_instance_id='SPORTS_BASELINE_ARB_PRIMARY'")
        db.conn.execute("UPDATE engine_instances SET engine_instance_id='RACING_LEGACY_SIMPLE_PRIMARY',engine_type='LEGACY_SIMPLE_ARB' WHERE engine_instance_id='GREYHOUNDS_BASELINE_ARB_PRIMARY'")
        db.conn.execute("UPDATE engine_configs SET engine_instance_id='RACING_LEGACY_SIMPLE_PRIMARY' WHERE engine_instance_id='GREYHOUNDS_BASELINE_ARB_PRIMARY'")
        db.conn.commit()
    db.conn.close()

    upgraded = DB(path)
    upgraded.ensure_default_engines()
    after = upgraded.get_setting("config", {})
    assert after["matched_market_archive_enabled"] is True
    assert after["matched_market_archive_runtime_gate_required"] is True
    assert after["matched_market_archive_required_before_prune"] is False
    assert after["sentinel_0915_upgrade"] == "keep"
    assert upgraded.get_setting("archive_runtime_pause_until") == "2026-08-13T19:00:00+00:00"
    assert sentinel.read_bytes() == b"archive-must-survive"
    assert (root / "_last_runtime_gate.json").exists()
    ids = {r["engine_instance_id"] for r in upgraded.engine_instances()}
    assert "SPORTS_BASELINE_ARB_PRIMARY" in ids and "GREYHOUNDS_BASELINE_ARB_PRIMARY" in ids
    assert "SPORTS_LEGACY_SIMPLE_PRIMARY" not in ids and "RACING_LEGACY_SIMPLE_PRIMARY" not in ids
