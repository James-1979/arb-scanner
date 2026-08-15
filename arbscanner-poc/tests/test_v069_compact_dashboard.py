from pathlib import Path


def test_recent_opportunities_use_compact_rows():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert 'id="recentCards" class="opplist terminal-list"' in html
    assert 'class="oppcompact"' in html
    assert 'class="opptitle"' in html
    assert '>Profit</span><strong class="good">+' in html
    assert '>ROI used</span><strong>' in html
    assert 'class="oppchev"' in html
    assert 'Click to expand' not in html


def test_collapsed_rows_do_not_render_full_money_grid():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    start = html.index('function renderRecent(rows)')
    end = html.index('function openOpportunityDrawer', start)
    render_recent = html[start:end]
    assert 'moneyhero' not in render_recent
    assert 'moneygrid' not in render_recent
    assert 'notice warnbox' not in render_recent
    assert 'Delayed BF' in render_recent
