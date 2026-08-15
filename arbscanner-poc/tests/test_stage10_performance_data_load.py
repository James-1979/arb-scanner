import json
from pathlib import Path

from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]


def _read_query_count(statements):
    return sum(1 for statement in statements if statement.lstrip().split(None, 1)[0].upper() in {"SELECT", "WITH", "PRAGMA"})


def _write_count(statements):
    return sum(1 for statement in statements if statement.lstrip().split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER", "VACUUM"})


def test_stage10_performance_manifest_has_hard_racing_monitor_budget():
    payload = json.loads((ROOT / "validation" / "refactor_performance_targets.json").read_text(encoding="utf-8"))
    assert payload["stage"] == 10
    assert payload["immutable_parent_stage"] == 9
    target = payload["targets"]["racing_monitor"]
    assert target["baseline_queries_per_projection"] == 100
    assert target["maximum_candidate_queries_per_projection"] <= 10
    assert target["maximum_candidate_write_statements"] == 0
    assert target["required_output_fingerprint_change"] is False
    assert "No new stateful cache" in payload["cache_policy"]


def test_rows_only_racing_overview_skips_unrelated_dashboard_and_portfolio_work(tmp_path, monkeypatch):
    api = API(tmp_path / "stage10-rows-only.sqlite3")
    monkeypatch.setattr(api, "dashboard_overview", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dashboard must not load")))
    monkeypatch.setattr(api, "_sim_portfolio_financial_state", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("portfolio must not load")))
    out = api.racing_overview({"limit": 1000, "_matched_rows_only": True})
    assert out == {"ok": True, "mode": "sim", "rows": []}


def test_racing_monitor_preserves_overview_matched_detail_compatibility_seam(tmp_path, monkeypatch):
    api = API(tmp_path / "stage10-monitor-seam.sqlite3")
    api.db.set_setting("racing_discovery_latest", {
        "rows": [{"exchange": "Betfair delayed", "market_id": "m1", "matched_event_key": "evt-1", "match_status": "matched"}],
        "summary": {"total": 1, "matched": 1, "unmatched": 0, "rejected": 0, "by_exchange": {"Betfair delayed": 1}},
    })
    matched = {
        "event_key": "evt-1", "event_name": "Romford", "event_start": "2026-08-15T18:00:00+00:00",
        "net_roi_pct": 1.25, "status": "racing_monitor", "best_price_book_pct": 98.75,
        "best_combined_book_pct": 98.75, "selection_basis": "best_book", "book_analysis": {},
        "data_quality": {"band": "Usable"}, "reference_stakes": [],
    }
    seen = {}
    def fake_overview(data=None):
        seen.update(data or {})
        return {"ok": True, "rows": [matched]}
    monkeypatch.setattr(api, "racing_overview", fake_overview)
    row = api.racing_monitor({})["rows"][0]
    assert seen.get("_matched_rows_only") is True
    assert row["price_state"] == "ready"
    assert row["net_roi_pct"] == 1.25
    assert row["research_status"] == "racing_monitor"


def test_racing_monitor_empty_fixture_stays_under_query_budget_and_read_only(tmp_path):
    api = API(tmp_path / "stage10-monitor-budget.sqlite3")
    statements = []
    api.db.conn.set_trace_callback(statements.append)
    out = api.racing_monitor({})
    api.db.conn.set_trace_callback(None)
    assert out["ok"] is True
    assert _read_query_count(statements) <= 10
    assert _write_count(statements) == 0
