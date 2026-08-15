from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
STYLE = HTML.split("<style>", 1)[1].split("</style>", 1)[0]


def test_v0838_release_ui_contract():
    assert __version__ == "0.9.36"
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML
    assert 'Weekly market heatmap' in HTML
    assert 'Monday → Sunday' in HTML
    assert 'market-week-heatmap' in STYLE
    assert 'id="marketSportsPreDiscovery"' in HTML
    assert 'id="marketSportsInplayDiscovery"' in HTML
    assert 'id="marketRacingDiscovery"' in HTML
    assert 'id="timelineReplayPnlChart"' not in HTML
    assert 'Running P&amp;L</strong>' not in HTML
    assert 'capital-area' in STYLE
    assert 'cumulative-area' in STYLE
    assert 'hover-band' in STYLE


def test_market_heatmap_returns_week_cells(tmp_path):
    api = API(tmp_path / "weekly.sqlite3")
    # Monday 00:00 UTC through next Monday 00:00 UTC gives a full 7x24 matrix.
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    result = api.market_heatmap({
        "from_utc": start.isoformat(), "to_utc": end.isoformat(),
        "timezone_offset_minutes": 0, "timezone_name": "UTC",
    })
    assert result["ok"] is True
    assert len(result["cells"]) == 168
    assert [result["cells"][i * 24]["day_label"] for i in range(7)] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert [result["cells"][i]["hour"] for i in range(24)] == list(range(24))
    # Keep backwards-compatible 24-hour aggregate for existing integrations.
    assert len(result["hours"]) == 24


def test_replay_financial_values_remain_on_timeline_without_duplicate_chart():
    assert 'timeline-return-marker${cls}${selected}${labeled}' in HTML
    assert '<span class="return-value">${esc(signedGbp(pnl))}</span>' in HTML
    assert 'id="timelineReplayPnlChart"' not in HTML
