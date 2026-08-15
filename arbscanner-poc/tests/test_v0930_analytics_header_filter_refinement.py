from pathlib import Path

from arbscanner import __version__

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
INSTALLER = (ROOT / "BUILD_AND_INSTALL.command").read_text()


def test_v0930_release_identity_and_installer_lock():
    assert __version__ == "0.9.39"
    assert '<title>ArbScanner PoC 0.9.39</title>' in HTML
    assert 'EXPECTED_VERSION="0.9.39"' in INSTALLER
    assert 'Extract the 0.9.39 package and run its installer there.' in INSTALLER


def test_v0930_replay_has_no_local_mode_filter_and_follows_global_context():
    assert 'id="timelineReplayMode0917"' not in HTML
    assert "mode=String(typeof dataContextMode==='string'?dataContextMode:'sim').toLowerCase()" in HTML
    assert "rowMode0917(x)===mode" in HTML
    assert 'id="timelineReplayEngine0917"' in HTML
    assert 'id="timelineReplayVenue0917"' in HTML
    assert 'id="timelineReplayAccount0917"' in HTML
    assert 'id="timelineReplaySearch"' in HTML


def test_v0930_performance_filters_are_owned_by_analytics_header_context():
    assert 'class="performance-header-filter-host0930"' in HTML
    assert 'performance-header-filters0930' in HTML
    assert "performanceFilters=document.querySelector('.performance-header-filter-host0930')" in HTML
    assert "if(name==='performance'&&performanceFilters)" in HTML
    assert "context.appendChild(performanceFilters)" in HTML
    assert '.analytics-viewhead .performance-header-filters0930{display:flex!important' in HTML
    assert 'performance-filter-support0930' in HTML


def test_v0930_replay_stable_detail_boundary_is_preserved():
    assert 'grid-template-rows:auto auto auto auto auto auto minmax(156px,1fr) auto 72px auto!important' in HTML
    assert '.timeline-event-detail.replay-detail-open{height:72px!important;min-height:72px!important;max-height:72px!important' in HTML
