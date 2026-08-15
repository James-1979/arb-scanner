from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
DB_SOURCE = (ROOT / "arbscanner" / "db.py").read_text(encoding="utf-8")
RELEASE_NOTES = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")
INSTALLER = (ROOT / "BUILD_AND_INSTALL.command").read_text(encoding="utf-8")


def _v0947_script() -> str:
    return HTML.split('<script id="v0947-timeline-heatmap-js">', 1)[1].split("</script>", 1)[0]


def test_0947_release_identity_and_notes():
    assert __version__ == "0.9.47"
    assert "<title>ArbScanner PoC 0.9.47</title>" in HTML
    assert 'EXPECTED_VERSION="0.9.47"' in INSTALLER
    assert "## 0.9.47 — Timeline Interaction & Heatmap Integrity" in RELEASE_NOTES


def test_0947_heatmap_exposes_complete_operator_metric_set(tmp_path: Path):
    api = API(tmp_path / "heatmap.sqlite3")
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    finish = start + timedelta(days=7)
    api.db.market_heatmap_between = lambda *_args, **_kwargs: {
        "source": "test",
        "rollups": [{
            "hour_utc": start.isoformat(), "section": "sports", "sport": "Football",
            "market_name": "Match Odds", "in_play": 0, "observations": 5,
            "unique_markets": 2, "net_positive": 1,
        }],
        "financial": [{
            "hour_utc": start.isoformat(), "section": "sports", "sport": "Football",
            "market_name": "Match Odds", "in_play": 0, "qualified": 1, "executed": 1,
            "deployed": 25.0, "settled": 1, "settled_deployed": 25.0, "pnl": 2.5,
        }],
        "liquidity_depth": [{
            "hour_utc": start.isoformat(), "section": "sports", "sport": "Football",
            "market_name": "Match Odds", "in_play": 0, "depth_samples": 2,
            "top_book_depth_sum": 200.0, "top3_depth_sum": 600.0,
        }],
        "liquidity_opportunity": [{
            "hour_utc": start.isoformat(), "section": "sports", "sport": "Football",
            "market_name": "Match Odds", "in_play": 0, "liquidity_capable": 3,
            "liquidity_rejected": 1, "executable_stake_sum": 120.0,
            "executable_stake_samples": 3,
        }],
    }
    result = api.market_heatmap({
        "from_utc": start.isoformat(), "to_utc": finish.isoformat(),
        "scope": "all", "phase": "all", "timezone_name": "UTC",
        "timezone_offset_minutes": 0,
    })
    expected = {
        "observations", "unique_markets", "net_positive", "qualified", "executed", "settled",
        "deployed", "settled_deployed", "pnl", "roi_pct", "available_depth", "top_book_depth",
        "avg_executable_stake", "liquidity_capable", "liquidity_rejected",
        "liquidity_rejection_rate_pct",
    }
    assert result["ok"] is True
    assert set(result["metrics"]) == expected
    assert result["application_mode"] == "sim"
    assert result["metric_ownership"]["observations"] == "shared"
    assert result["metric_ownership"]["pnl"] == "sim"
    cell = next(x for x in result["cells"] if x["observed"])
    assert cell["top_book_depth"] == 100.0
    assert cell["available_depth"] == 300.0
    assert cell["liquidity_rejected"] == 1
    assert cell["settled"] == 1
    assert cell["settled_deployed"] == 25.0


def test_0947_live_heatmap_keeps_lifecycle_actual_live_and_sport_diagnostic_isolated(tmp_path: Path):
    api = API(tmp_path / "live-heatmap.sqlite3")
    stamp = "2026-08-10T12:00:00+00:00"
    base_cell = {
        "date": "2026-08-10", "day_index": 0, "day_label": "Mon", "hour": 12,
        "observed": True, "observations": 10, "unique_markets": 4, "net_positive": 2,
        "qualified": 0, "executed": 0, "settled": 0, "deployed": 0.0,
        "settled_deployed": 0.0, "pnl": 0.0, "roi_pct": 0.0,
        "depth_samples": 1, "top_book_depth_sum": 100.0, "top3_depth_sum": 300.0,
        "executable_stake_sum": 50.0, "executable_stake_samples": 1,
        "liquidity_capable": 2, "liquidity_rejected": 1,
        "available_depth": 300.0, "top_book_depth": 100.0,
        "avg_executable_stake": 50.0, "liquidity_rejection_rate_pct": 33.3333,
    }
    api.market_heatmap = lambda _data=None: {
        "ok": True, "cells": [dict(base_cell)],
        "by_sport": {"Football": [dict(base_cell)], "Tennis": [dict(base_cell)]},
        "sports": ["Football", "Tennis"], "hours": [], "metrics": [],
        "source": "test", "metric_ownership": {},
    }
    api.db.live_decision_analytics = lambda *_args, **_kwargs: {
        "hourly": [{"hour_utc": stamp, "qualified": 5}],
        "hourly_by_sport": [
            {"hour_utc": stamp, "sport": "Football", "qualified": 2},
            {"hour_utc": stamp, "sport": "Tennis", "qualified": 3},
        ],
    }
    result = api.live_market_heatmap({
        "from_utc": "2026-08-10T00:00:00+00:00", "to_utc": "2026-08-11T00:00:00+00:00",
        "scope": "sports", "phase": "all", "timezone_name": "UTC", "timezone_offset_minutes": 0,
    })
    assert result["application_mode"] == "live"
    assert result["metric_ownership"]["qualified"] == "live"
    assert result["cells"][0]["qualified"] == 0
    assert result["cells"][0]["executed"] == 0
    assert result["cells"][0]["pnl"] == 0.0
    assert result["cells"][0]["decision_qualified_evidence"] == 5
    assert result["by_sport"]["Football"][0]["decision_qualified_evidence"] == 2
    assert result["by_sport"]["Tennis"][0]["decision_qualified_evidence"] == 3

    phase_specific = api.live_market_heatmap({
        "from_utc": "2026-08-10T00:00:00+00:00", "to_utc": "2026-08-11T00:00:00+00:00",
        "scope": "sports", "phase": "in_play", "timezone_name": "UTC", "timezone_offset_minutes": 0,
    })
    assert phase_specific["cells"][0]["decision_qualified_evidence"] == 0


def test_0947_sim_financial_heatmap_query_is_fail_closed_to_sim(tmp_path: Path):
    api = API(tmp_path / "mode-lock.sqlite3")
    with pytest.raises(ValueError, match="SIM lifecycle data only"):
        api.db._financial_hour_rows_between(
            "2026-08-10T00:00:00+00:00", "2026-08-11T00:00:00+00:00", mode="live"
        )
    assert DB_SOURCE.count("LOWER(COALESCE(mp.mode,'sim'))='sim'") >= 2


def test_0947_frontend_heatmap_metrics_and_mode_semantics_are_complete():
    for value in (
        "unique_markets", "net_positive", "settled", "settled_deployed", "top_book_depth",
        "liquidity_rejected",
    ):
        assert f'<option value="{value}">' in HTML
    js = _v0947_script()
    assert "marketHeatMetricMeta0947" in js
    assert "Shared provider market evidence" in js
    assert "SIM lifecycle/economic state" in js
    assert "Actual LIVE lifecycle/economic state" in js
    assert "x&&x.observed" in js


def test_0947_performance_capital_usage_and_direct_drag_contract():
    js = _v0947_script()
    assert "Capital availability &amp; usage" in HTML
    assert "Capital in Use" in HTML
    assert "capital-in-use-band0947" in js
    assert "Peak capital in use" in js
    assert "performanceCapitalUse0947" in js
    assert "performanceSeekFraction0947" in js
    assert "installPerformanceDrag0947" in js
    assert "pointerdown" in js and "pointermove" in js


def test_0947_replay_timeline_drag_and_speed_contract():
    js = _v0947_script()
    assert "replayPlaybackDurationMs0947" in js
    assert "60000/Math.max(.5" in js
    assert "setReplaySpeed0940=function" in js
    assert "timelineReplayPlay=function" in js
    assert "installReplayDrag0947" in js
    assert "timelineReplayPointerFraction0947" in js
    assert "Click or drag anywhere on the timeline to scrub" in js
    for speed in (".5", "1", "2", "5", "10"):
        assert speed in js
