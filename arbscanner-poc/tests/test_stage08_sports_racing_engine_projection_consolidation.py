from __future__ import annotations

from pathlib import Path

from arbscanner.api import API
from arbscanner.operator_projection import (
    engine_catalog_row,
    engine_catalog_visible,
    merge_engine_lifecycle_groups,
    operator_domain,
    project_engine_lifecycle,
)

ROOT = Path(__file__).resolve().parents[1]


def test_operator_domain_never_widens_unknown_domain_across_sports_and_racing():
    assert operator_domain("sports") == "sports"
    assert operator_domain("racing") == "racing"
    assert operator_domain("all") == "sports"


def test_engine_catalog_projection_is_pure_and_reference_filter_is_shared():
    source = {
        "engine_instance_id": "SPORTS_A",
        "engine_type": "SPORTS_BASELINE_ARB",
        "package_source": "builtin",
        "capabilities": ["ARBITRAGE"],
    }
    projected = engine_catalog_row(source, {"reference_only": False, "package_origin": "builtin_registry"}, {"pnl": 1.25})
    assert projected["package_origin"] == "builtin_registry"
    assert projected["performance"] == {"pnl": 1.25}
    assert engine_catalog_visible(projected) is True
    assert "reference_only" not in source and "performance" not in source

    hidden = engine_catalog_row(source, {"reference_only": True}, {})
    assert engine_catalog_visible(hidden) is False
    assert engine_catalog_visible(hidden, include_reference=True) is True


def test_multi_sport_engine_lifecycle_merge_uses_immutable_engine_identity():
    groups = [
        [{"engine_instance_id": "A", "processed": 2, "opportunities": 1, "qualified": 1, "executed": 1,
          "settled": 0, "errors": 0, "realised_pnl": 0.0, "last_activity": "2026-08-15T10:00:00+00:00"}],
        [{"engine_instance_id": "A", "processed": 3, "opportunities": 2, "qualified": 1, "executed": 0,
          "settled": 1, "errors": 1, "realised_pnl": 1.23456, "last_activity": "2026-08-15T11:00:00+00:00"},
         {"engine_instance_id": "B", "processed": 4, "opportunities": 0, "qualified": 0, "executed": 0,
          "settled": 0, "errors": 0, "realised_pnl": 0.0, "last_activity": None}],
    ]
    rows = merge_engine_lifecycle_groups(groups)
    by_id = {row["engine_instance_id"]: row for row in rows}
    assert by_id["A"]["processed"] == 5
    assert by_id["A"]["opportunities"] == 3
    assert by_id["A"]["qualified"] == 2
    assert by_id["A"]["executed"] == 1
    assert by_id["A"]["settled"] == 1
    assert by_id["A"]["errors"] == 1
    assert by_id["A"]["realised_pnl"] == 1.2346
    assert by_id["A"]["last_activity"] == "2026-08-15T11:00:00+00:00"
    assert by_id["B"]["processed"] == 4


def test_live_engine_lifecycle_preserves_decision_evidence_but_fails_qualified_closed():
    source = [{
        "engine_instance_id": "SPORTS_A", "processed": 10, "opportunities": 5,
        "qualified": 4, "executed": 0, "settled": 0, "errors": 1, "realised_pnl": 0.0,
    }]
    rows, totals = project_engine_lifecycle(source, mode="live")
    assert rows[0]["processed"] == 10 and rows[0]["opportunities"] == 5
    assert rows[0]["decision_qualified_evidence"] == 4
    assert rows[0]["qualified"] == 0
    assert totals["decision_qualified_evidence"] == 4
    assert totals["qualified"] == 0
    assert source[0]["qualified"] == 4


def test_engine_filter_contract_is_identical_for_sports_and_racing_projection():
    source = [
        {"engine_instance_id": "A", "processed": 1, "opportunities": 0, "qualified": 0, "executed": 0, "settled": 0, "errors": 0, "realised_pnl": 0},
        {"engine_instance_id": "B", "processed": 2, "opportunities": 0, "qualified": 0, "executed": 0, "settled": 0, "errors": 0, "realised_pnl": 0},
    ]
    rows, totals = project_engine_lifecycle(source, mode="sim", engine_filter="B")
    assert [row["engine_instance_id"] for row in rows] == ["B"]
    assert totals["processed"] == 2


def test_api_engine_lifecycle_keeps_section_scoped_and_multi_sport_merge_inside_sports(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    api = API(tmp_path / "stage08.sqlite3")
    calls = []

    def fake_rows(**kwargs):
        calls.append(dict(kwargs))
        sport = kwargs.get("sport")
        return [{
            "engine_instance_id": "E", "engine_type": "TYPE", "nickname": "Engine", "state": "ACTIVE", "enabled": True,
            "processed": 1 if sport == "Football" else 2, "opportunities": 1, "qualified": 1,
            "executed": 0, "settled": 0, "realised_pnl": 0.0, "errors": 0, "last_activity": str(sport),
            "latency_ms": 0.0, "streams": [], "provenance_authority": "test",
        }]

    monkeypatch.setattr(api.db, "engine_lifecycle_rows", fake_rows)
    sports = api.engine_lifecycle({"section": "sports", "mode": "sim", "sports": ["Football", "Tennis"]})
    assert sports["totals"]["processed"] == 3
    assert {call["section"] for call in calls} == {"sports"}
    assert [call["sport"] for call in calls] == ["Football", "Tennis"]

    calls.clear()
    racing = api.engine_lifecycle({"section": "racing", "mode": "sim", "sport": "Greyhounds"})
    assert racing["section"] == "racing"
    assert calls == [dict(section="racing", mode="sim", from_utc=None, to_utc=None, stream="all", market="", search="", venue="all", account="all", sport="Greyhounds")]


def test_live_racing_overview_remains_hard_locked_even_with_decision_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    api = API(tmp_path / "racing-live.sqlite3")
    monkeypatch.setattr(api, "_live_portfolio_financial_state_async", lambda *_a, **_k: _async_value({"capital": None}))
    monkeypatch.setattr(api, "live_decision_evidence", lambda *_a, **_k: {"latest": [{"event_name": "Race", "market_name": "Win", "net_roi_pct": 5}], "summary": {"qualified": 9}})
    monkeypatch.setattr(api.db, "get_setting", lambda key, default=None: ({"rows": []} if key == "racing_discovery_latest" else default))
    monkeypatch.setattr(api, "_operational_status", lambda *_a, **_k: {})
    result = api.racing_overview({"mode": "live"})
    assert result["live_execution_allowed"] is False
    assert result["active_positions"] == 0
    assert result["positions"] == [] and result["highlights"] == [] and result["rows"] == []
    assert result["summary"]["qualified_monitor"] == 0
    assert result["summary"]["decision_qualified_evidence"] == 9


async def _async_value(value):
    return value


def test_operator_projection_module_is_pure_and_has_no_db_provider_or_scanner_dependency():
    import arbscanner.operator_projection as projection

    source = (ROOT / "arbscanner" / "operator_projection.py").read_text(encoding="utf-8")
    assert not hasattr(projection, "DB")
    assert "from .db import" not in source
    assert "from .live_providers import" not in source
    assert "from .scanner import" not in source
