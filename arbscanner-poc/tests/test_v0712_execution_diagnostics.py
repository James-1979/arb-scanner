from dataclasses import asdict
from pathlib import Path

from arbscanner.api import API
from arbscanner.models import Leg


def _legs():
    return [
        Leg("Matchbook", "Home", 2.1, 100.0, 2.0, event_id="mb-e", market_id="mb-m", selection_id="h"),
        Leg("Betfair delayed", "Away", 2.1, 100.0, 2.0, event_id="bf-e", market_id="bf-m", selection_id="a"),
    ]


def _opportunity(api: API, suffix: str) -> int:
    return api.db.add_opportunity(
        f"evt-{suffix}", f"A v B {suffix}", "2030-01-01T12:00:00+00:00", "Match Odds", 3, 3,
        [asdict(x) for x in _legs()], [], .99, suffix,
    )


def test_missed_monitor_value_is_not_execution_leakage(tmp_path: Path):
    api = API(tmp_path / "diag.sqlite3")
    oid = _opportunity(api, "missed")
    api.db.add_execution_run(
        oid, mode="monitor", execution_type="modeled_monitor", state="MONITOR_MISSED",
        deployed=100.0, expected_profit=6.852, captured_profit=0.0,
        details={
            "first_failure_reason": "PRICE_MOVED",
            "observations": [
                {"offset_ms": 100, "still_executable": False, "failure_reason": "PRICE_MOVED", "fetch_latency_ms": 129},
                {"offset_ms": 250, "still_executable": False, "failure_reason": "PRICE_MOVED", "fetch_latency_ms": 110},
            ],
        },
        started_at="2026-08-09T22:48:59+00:00",
    )
    result = api.activity_analytics({"mode": "monitor", "limit": 100})
    row = result["executions"][0]
    assert row["diagnostics"]["missed"] is True
    assert row["diagnostics"]["executed"] is False
    assert row["diagnostics"]["reason"] == "PRICE_MOVED"
    assert row["diagnostics"]["opportunity_lost"] == 6.852
    assert row["diagnostics"]["execution_leakage"] == 0.0
    counts = result["execution_counts"]["monitor"]
    assert counts["missed"] == 1
    assert counts["executed"] == 0
    assert counts["opportunity_lost"] == 6.852
    assert counts["execution_leakage"] == 0.0
    # Raw historical field remains available for backwards compatibility/debugging.
    assert counts["leakage"] == 6.852


def test_open_position_keeps_execution_leakage(tmp_path: Path):
    api = API(tmp_path / "exec.sqlite3")
    oid = _opportunity(api, "open")
    api.db.add_execution_run(
        oid, mode="monitor", execution_type="modeled_monitor", state="MONITOR_OPEN",
        deployed=50.0, expected_profit=5.0, captured_profit=4.25,
        details={"monitor_position_opened": True, "execution_result": {"fills": [{"stake": 10.0}]}},
        started_at="2026-08-09T22:49:00+00:00",
    )
    result = api.activity_analytics({"mode": "monitor", "limit": 100})
    row = result["executions"][0]
    assert row["diagnostics"]["executed"] is True
    assert row["diagnostics"]["missed"] is False
    assert row["diagnostics"]["opportunity_lost"] == 0.0
    assert row["diagnostics"]["execution_leakage"] == 0.75
    counts = result["execution_counts"]["monitor"]
    assert counts["executed"] == 1
    assert counts["execution_leakage"] == 0.75


def test_frontend_labels_missed_value_and_reason_professionally():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "PoC 0.9.36" in html
    assert "Opportunity lost" in html
    assert "Value at detection" in html
    assert "failureReasonLabel" in html
    assert "This is not a trading loss" in html
