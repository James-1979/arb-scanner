from pathlib import Path

from arbscanner import __version__

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
INSTALLER = (ROOT / "BUILD_AND_INSTALL.command").read_text()


def test_v0928_release_and_installer_are_locked_together():
    assert __version__ == "0.9.39"
    assert '<title>ArbScanner PoC 0.9.39</title>' in HTML
    assert 'EXPECTED_VERSION="0.9.39"' in INSTALLER
    assert 'Extract the 0.9.39 package and run its installer there.' in INSTALLER


def test_v0928_replay_has_running_realised_pnl_at_the_playhead():
    assert 'id="timelineReplayRunningPnl0928"' in HTML
    assert 'id="timelineReplayRunningMeta0928"' in HTML
    assert 'function replayRunningPnl0928(atMs)' in HTML
    assert 'p.settledAt.getTime()<=time' in HTML
    assert 'pnl=settled.reduce((n,p)=>n+Number(p.pnl||0),0)' in HTML
    assert 'timelineReplayUpdateMetrics=function(atMs){return replayRunningPnl0928(atMs)}' in HTML
    # Opening positions must not affect realised running P&L.
    block = HTML.split('function replayRunningPnl0928(atMs)', 1)[1].split('// Playback now updates one realised running total.', 1)[0]
    assert 'p.start' not in block


def test_v0928_replay_lifecycle_is_explained_in_the_canvas():
    assert 'function replayLifecycleLegend0928()' in HTML
    assert '>Opened</span>' in HTML
    assert '>Position active</span>' in HTML
    assert '>Settled P&amp;L</span>' in HTML
    assert 'Emergency hedge</span>' in HTML
    assert 'replay-legend-dot0928' in HTML
    assert 'replay-legend-line0928' in HTML


def test_v0928_settlement_labels_are_collision_safe_and_selected_wins():
    block = HTML.split('function replayApplyCollisionSafeLabels0928()', 1)[1].split('function replayRunningPnl0928', 1)[0]
    assert "markers.forEach(m=>m.classList.remove('labeled'))" in block
    assert 'Number(b.selected)-Number(a.selected)' in block
    assert 'Math.abs(a.x-c.x)<a.half+c.half+14' in block
    assert "c.m.classList.add('labeled')" in block
    assert 'accepted.filter(x=>!x.selected).length>=6' in block


def test_v0928_selected_position_detail_has_five_dedicated_metric_columns():
    assert '.timeline-detail0928 .timeline-event-values{display:grid!important;grid-template-columns:repeat(5,minmax(74px,1fr))!important' in HTML
    block = HTML.rsplit('renderTimelineReplayEventDetail=function(p){', 1)[1].split('</script>', 1)[0]
    for label in ('Actual result', 'Capital', 'Returned', 'Final P&amp;L', 'Structure'):
        assert f'<span>{label}</span>' in block
    assert 'rowEngine0917(x)' in block
    assert 'rowAccount0917(x)' in block
    assert 'rowMode0917(x).toUpperCase()' in block
    assert 'timeline-detail-actions0928' in block
