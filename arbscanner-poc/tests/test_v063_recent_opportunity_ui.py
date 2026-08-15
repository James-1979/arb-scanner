from pathlib import Path


def test_recent_opportunities_open_drawer_and_show_sport_badge():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert 'id="oppDrawer"' in html
    assert 'openOpportunityDrawer(${x.id})' in html
    assert 'function sportPill(sport)' in html
    assert 'class="sporttag"' in html
    assert "loadOpportunity(Number(id),'drawerBody',false,true)" in html
    assert 'data-opportunity-id=' in html
    assert "document.addEventListener('click'" in html
    assert 'id="recentCards" class="opplist terminal-list"' in html


def test_frontend_and_backend_versions_match_065():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    init_py = (Path(__file__).parents[1] / "arbscanner" / "__init__.py").read_text()
    api_py = (Path(__file__).parents[1] / "arbscanner" / "api.py").read_text()
    assert "0.9.36" in html
    assert '__version__ = "0.9.36"' in init_py
    assert '"version": "0.9.36"' in api_py
