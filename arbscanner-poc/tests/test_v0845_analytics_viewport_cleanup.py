from pathlib import Path

from arbscanner import __version__

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_v0845_release_identity_and_single_analytics_viewport_owner():
    assert __version__ == "0.9.36"
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML
    assert 'v0.8.45 canonical Analytics viewport contract' in HTML
    assert "classList.toggle('analytics-viewport-fit',name==='market'||name==='replay')" in HTML
    assert "--app-top-height" in HTML


def test_v0845_removes_obsolete_replay_market_height_patch_layers():
    # These were the conflicting historical patch points that caused tall-window
    # dead space and short-window clipping. They must not return as overrides.
    forbidden = (
        'id="v0842-replay-density"',
        'id="v0842-market-discovery-height"',
        'height:calc(100dvh - 167px)!important',
        'height:225px',
        'height:215px',
        'grid-template-rows:auto auto auto auto auto auto 110px',
        'min-height:190px!important',
    )
    for token in forbidden:
        assert token not in HTML


def test_v0845_market_and_replay_have_elastic_desktop_regions():
    assert 'market-hour-card{flex:1 1 250px;min-height:250px' in HTML
    assert 'market-discovery-grid{flex:0 0 158px;min-height:158px' in HTML
    assert 'grid-template-rows:repeat(8,minmax(17px,1fr))' in HTML
    assert 'grid-template-rows:auto auto auto auto auto auto minmax(132px,1fr)' in HTML
    assert 'timeline-canvas-wrap{height:100%;min-height:132px' in HTML


def test_v0845_replay_marker_geometry_follows_live_canvas_height():
    assert 'canvas.clientHeight||132' in HTML
    assert "canvas.style.setProperty('--timeline-axis-y'" in HTML
    assert 'top=openBase+p.startLane*openGap' in HTML
    assert 'top=returnBase+lane*returnGap' in HTML
    assert "currentAnalyticsPane()==='replay'" in HTML
