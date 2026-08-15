from pathlib import Path


def test_primary_navigation_is_scanner_activity_analytics_settings():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    nav = html[html.index('<div class="nav" id="nav"'):html.index('</div>', html.index('<div class="nav" id="nav"')) + 6]
    assert '>Scanner</span>' in nav
    assert '>Activity</span>' in nav
    assert '>Analytics</span>' in nav
    assert '>Settings</span>' in nav
    assert '>Jobs</span>' not in nav
    assert 'Matched Markets' not in nav


def test_scanner_screen_is_rules_then_optional_monitor_timing_activation():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert 'Betting rules' in html
    assert 'id="runMinProfit"' in html
    assert 'id="runMinRoi"' in html
    assert 'id="runMaxStake"' in html
    assert 'scanner always runs in WATCH' in html
    assert 'id="monitor_timingActionBtn"' in html
    assert 'ACTIVATE MONITOR_TIMING' in html
    assert 'ACTIVATE LIVE BETTING' in html
    assert 'Latest qualifying opportunities' in html
    assert 'class="opplist terminal-list"' in html


def test_activity_is_journal_not_jobs_page():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert '>Activity</h1>' in html
    assert '>Jobs</h1>' not in html
    assert 'data-operator-tab="opportunities"' in html
    assert 'data-operator-tab="executions"' in html
    assert 'data-operator-tab="results"' in html
    assert 'id="oppDrawer"' in html


def test_accounts_owns_provider_management_and_admin_is_system_only():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert 'Connections & credentials' in html
    assert 'Advanced account settings & SIM funding' in html
    assert 'System health & export' in html
    assert 'Maintenance & reset' in html
    assert 'Exchange connections' not in html
    assert "['Matched-market research',$('matched')]" not in html
