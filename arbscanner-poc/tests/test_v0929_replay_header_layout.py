from pathlib import Path

from arbscanner import __version__

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
INSTALLER = (ROOT / "BUILD_AND_INSTALL.command").read_text()


def test_v0929_release_identity_and_installer_lock():
    assert __version__ == "0.9.39"
    assert '<title>ArbScanner PoC 0.9.39</title>' in HTML
    assert 'EXPECTED_VERSION="0.9.39"' in INSTALLER
    assert 'Extract the 0.9.39 package and run its installer there.' in INSTALLER


def test_v0929_replay_filters_are_owned_by_the_analytics_header_context():
    assert 'class="replay-header-filter-host0929"' in HTML
    assert 'class="simplefilterbar timeline-replay-filterbar replay-header-filters0929"' in HTML
    assert "replayFilters=document.querySelector('.replay-header-filter-host0929')" in HTML
    assert "else if(name==='replay'&&replayFilters)" in HTML
    assert "context.appendChild(replayFilters)" in HTML
    assert '.analytics-viewhead .replay-header-filters0929{display:flex!important' in HTML


def test_v0929_replay_detail_row_is_reserved_and_cannot_resize_timeline_on_playback():
    assert 'grid-template-rows:auto auto auto auto auto auto minmax(156px,1fr) auto 72px auto!important' in HTML
    assert '.timeline-event-detail.replay-detail-open{height:72px!important;min-height:72px!important;max-height:72px!important' in HTML
    assert '.timeline-event-detail.replay-detail-open>.timeline-event-card{height:100%;box-sizing:border-box}' in HTML
    final = HTML.rsplit('renderTimelineReplayEventDetail=function(p){', 1)[1]
    assert "el.classList.add('replay-detail-open')" in final
    assert "el.classList.toggle('replay-detail-selected0929',!!p)" in final


def test_v0929_replay_keeps_0928_financial_and_lifecycle_clarity():
    assert 'id="timelineReplayRunningPnl0928"' in HTML
    assert 'function replayLifecycleLegend0928()' in HTML
    assert 'function replayApplyCollisionSafeLabels0928()' in HTML
    assert 'timeline-detail0928' in HTML
