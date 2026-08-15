from pathlib import Path

from arbscanner import __version__

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_v0844_release_identity_tracks_current_release():
    assert __version__ == "0.9.36"
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML
    assert 'v0.8.45 decision-focused Performance dashboard' in HTML


def test_performance_uses_financial_timeline_and_secondary_decision_sections():
    assert 'Financial Timeline' in HTML
    assert 'Capital &amp; Exposure' in HTML
    assert 'Realised P&amp;L' in HTML
    assert 'Settled Turnover' in HTML
    assert 'Captured Edge' in HTML
    assert 'performanceCapitalTimeline0931' in HTML
    assert 'performancePnlTimeline0931' in HTML
    assert 'performanceDomainGrid' in HTML
    assert 'performanceMarketBody' in HTML
    assert 'performanceVenueBody' in HTML
    assert 'performancePairBody' in HTML
    assert 'performanceFunnel' in HTML
    assert 'class="performance-chart-grid-0840"' not in HTML


def test_performance_chart_is_interactive_but_restrained():
    assert 'performanceTimelineHover0931' in HTML
    assert 'performanceTimelineTooltip0931' in HTML
    assert 'perfMetricChart0844' in HTML
    assert 'chart-callout' not in HTML.split('<style id="v0844-performance-replay-market-polish">',1)[1].split('</style>',1)[0]
    assert 'peak-band' not in HTML.split('<style id="v0844-performance-replay-market-polish">',1)[1].split('</style>',1)[0]
