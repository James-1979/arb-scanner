from pathlib import Path


def _html():
    return Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()


def test_sidebar_primary_navigation_and_sports_workspace():
    html = _html()
    nav = html.split('<div class="nav" id="nav"', 1)[1].split('<section id="dashboard"', 1)[0]
    for label in ("Dashboard", "Active Positions", "Analytics", "Sports", "Racing", "Admin", "Help"):
        assert f">{label}<" in nav
    assert '<div class="nav-subgroup" aria-label="Sports navigation">' in nav
    for label in ("Overview", "Monitor"):
        assert f">{label}<" in nav
    assert ">Opportunities<" not in nav
    assert ">Execution History<" not in nav
    assert 'data-tab="replay"' not in nav
    assert 'data-nav-child="analytics" data-analytics-tab="replay"' in nav
    assert 'data-tab="racing"' in nav
    assert 'Racing</span><span class="navbadge">BETA' in nav
    assert '@media (min-width:861px)' in html
    assert 'width:188px' in html
    assert 'background:#0f172a' in html


def test_execution_history_remains_available_while_engine_nav_owns_domain_slot():
    html = _html()
    assert 'data-tab="monitor" data-nav-child="sports"' in html
    assert 'data-tab="sports-engines" data-nav-child="sports"' in html
    assert 'data-tab="racing-engines" data-nav-child="racing"' in html
    assert 'data-tab="sports-execution" data-nav-child="sports"' not in html
    assert 'data-tab="racing-execution" data-nav-child="racing"' not in html
    assert '<h1>Sports Monitor</h1>' in html
    assert 'data-analytics-pane="execution"' in html
    assert 'id="executionDomainTitle"' in html
    assert "let sportsPages=['sports','sports-engines','monitor','sports-config']" in html
    assert 'prepareInformationArchitecture();' in html


def test_dashboard_density_pass_keeps_diagnostics_but_hides_them_from_dashboard():
    html = _html()
    assert 'id="dashboardScanDetail"' in html
    assert '.dashboard-clean .dashboard-secondary-metric,.dashboard-clean .dashboard-scan-detail{display:none!important}' in html
    assert '.dashboard-clean #dashboardOps .opsitem:nth-child(4),.dashboard-clean #dashboardOps .opsitem:nth-child(6){display:none}' in html
    assert 'id="dashboardStreamComparison"' not in html


def test_current_ui_version_is_0727():
    html = _html()
    assert '<title>ArbScanner PoC 0.9.36</title>' in html
    assert 'PoC 0.9.36' in html
