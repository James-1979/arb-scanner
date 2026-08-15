from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'frontend' / 'index.html').read_text()


def test_version_is_0923():
    from arbscanner import __version__
    assert __version__ == '0.9.36'
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML


def test_installer_version_guard_matches_0923_package():
    installer = (ROOT / 'BUILD_AND_INSTALL.command').read_text()
    assert 'EXPECTED_VERSION="0.9.36"' in installer
    assert 'Extract the 0.9.36 package and run its installer there.' in installer
    assert 'EXPECTED_VERSION="0.9.22"' not in installer


def test_active_positions_have_compact_type_tiles_and_client_filtering():
    for key, label in [('all', 'All'), ('pre_match', 'Pre-match'), ('in_play', 'In-play'), ('racing', 'Racing')]:
        assert f'data-active-position-filter="{key}"' in HTML
        assert f">{label}</span>" in HTML
    assert "function setActivePositionFilter(filter)" in HTML
    assert "function activePositionType(x)" in HTML
    assert "renderActivePositions();updateActivePositionsNavCount" in HTML


def test_opportunity_detail_prioritises_event_date_and_start_time():
    assert 'function opportunityEventFocus(t)' in HTML
    assert 'Event date</span>' in HTML
    assert 'Start time</span>' in HTML
    assert '${detailHead}${opportunityEventFocus(o.event_timing)}' in HTML


def test_execution_analysis_keeps_secondary_evidence_but_collapses_it():
    assert '<details class="card execution-secondary-analysis">' in HTML
    assert 'Deeper execution analysis' in HTML
    assert 'Timing survival · execution paths · recovery economics' in HTML
    assert 'id="monitorTimingSurvivalCard"' in HTML
    assert 'id="executionPathTable"' in HTML
    assert 'id="executionHedgeProfitability"' in HTML


def test_individual_execution_timeline_is_on_demand():
    assert '<details class="execution-detail-timeline">' in HTML
    assert 'Critical economics stay above' in HTML
    assert 'Stored execution timeline' in HTML
