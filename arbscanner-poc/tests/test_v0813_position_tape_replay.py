from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_replay_is_period_centric_review_with_settlement_pnl_labels():
    assert "PoC 0.9.36" in HTML
    assert "Review what ArbScanner did across a selected period" in HTML
    assert 'id="timelineReplayPositions"' in HTML
    assert 'id="timelineReplayWon"' in HTML
    assert 'id="timelineReplayLost"' in HTML
    assert 'id="timelineReplayProfit"' in HTML
    assert 'id="timelineReplayLegs"' not in HTML
    assert 'id="timelineReplayPnlChart"' not in HTML
    assert 'id="timelineReplayPnlChartValue"' not in HTML
    assert "function buildTimelineReplayPositions(" in HTML
    assert "timeline-return-marker" in HTML
    assert "return-value" in HTML
    assert "function timelinePositionStructure(" in HTML
    assert "Opened" in HTML
    assert "Settled P&amp;L" in HTML
    assert "What happened when" in HTML


def test_replay_position_detail_exposes_individual_legs_and_recovery():
    assert "Position structure" in HTML
    assert "Base arb legs, scaled-entry fills, balancing/recovery fills and emergency hedges are shown separately." in HTML
    assert "Base leg" in HTML
    assert "Balance / recovery" in HTML
    assert "Emergency hedge" in HTML
    assert "HEDGE FILLED" in HTML
    assert "Execution detail" in HTML


def test_replay_supports_period_review_filters_and_position_filters():
    replay = HTML.split('<div class="analytics-pane" data-analytics-pane="replay">', 1)[1].split('<div class="analytics-pane" data-analytics-pane="scenarios">', 1)[0]
    for value in ("7d", "24h", "today", "custom"):
        assert f'<option value="{value}"' in replay
    for value in ("1h", "3h", "6h", "12h", "previous_day"):
        assert f'<option value="{value}"' not in replay
    assert '<option value="settled">Settled positions</option>' in replay
    assert '<option value="hedges">Emergency hedges</option>' in replay
    assert '<option value="open">Open positions</option>' in replay
