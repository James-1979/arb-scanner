import json
from dataclasses import asdict
from pathlib import Path

from arbscanner.api import API
from arbscanner.models import Leg


def test_export_opportunity_writes_shareable_json_and_csv(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    api = API(tmp_path / "arb.sqlite3")
    legs = [
        Leg("Betfair delayed", "Lucio Ratti", 1.77, 98.43, 6.0, market_id="bf-m", selection_id="ratti"),
        Leg("Matchbook", "Tai Leonard Sach", 2.78, 23.32, 2.0, market_id="mb-m", selection_id="sach"),
    ]
    oid = api.db.add_opportunity(
        "evt-1", "Lucio Ratti v Tai Leonard Sach", "2026-08-10T11:50:00+00:00", "Match Winner",
        5.0, 5.0, [asdict(x) for x in legs], [], 0.74, "export-test", sport="Tennis",
    )
    run_id = api.db.start_monitor_timing_run(
        oid, started_at="2026-08-10T11:50:43+00:00", initial_deployed=59.94,
        initial_profit=3.19, initial_roi_pct=5.32, planned_stakes=[], reference_checkpoint_ms=250,
    )
    api.db.add_monitor_timing_observation(
        run_id, offset_ms=100, elapsed_ms=129, observed_at="2026-08-10T11:50:43.129+00:00",
        fetch_latency_ms=129, deployed=0, expected_profit=0, expected_roi_pct=0,
        executable_fraction=0, full_stake_available=False, still_profitable=False, still_executable=False,
        failure_reason="EVENT_STARTED", quotes=[], venues=[
            {"exchange": "Betfair delayed", "market_id": "bf-m", "ok": True, "status": "OPEN", "in_play": True, "latency_ms": 90, "error": None},
            {"exchange": "Matchbook", "market_id": "mb-m", "ok": True, "status": "OPEN", "in_play": False, "latency_ms": 39, "error": None},
        ],
    )
    api.db.finish_monitor_timing_run(
        run_id, finished_at="2026-08-10T11:50:44+00:00", status="MISSED", survived_through_ms=0,
        first_failure_reason="EVENT_STARTED", reference_profit=0, reference_roi_pct=0, reference_executable=False,
    )

    result = api.export_opportunity({"opportunity_id": oid})
    assert result["ok"] is True
    assert len(result["files"]) == 3
    primary = Path(result["primary_file"])
    assert primary.exists()
    bundle = json.loads(primary.read_text())
    assert bundle["opportunity"]["event_name"] == "Lucio Ratti v Tai Leonard Sach"
    obs = bundle["monitor_timing_run"]["observations"][0]
    assert obs["failure_reason"] == "EVENT_STARTED"
    assert any(v["exchange"] == "Betfair delayed" and v["in_play"] is True for v in obs["venues"])
    assert Path(result["files"][1]).exists()
    assert Path(result["files"][2]).exists()


def test_frontend_exposes_opportunity_export_and_inplay_venue_detail():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "PoC 0.9.36" in html
    assert "Export diagnostic" in html
    assert "export_opportunity" in html
    assert "In-play confirmed by" in html


def test_missed_execution_with_later_result_is_not_labelled_settled_in_ui():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "let isSettled=x=>executionDiag(x).executed" in html
    assert "result=x.outcome?esc(x.outcome)" in html
