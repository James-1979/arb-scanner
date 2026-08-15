from pathlib import Path

ROOT = Path(__file__).parents[1]
HTML = ROOT.joinpath("frontend", "index.html").read_text()


def test_release_is_built_from_safe_v083_ui_foundation_without_settings_loss():
    assert "PoC 0.9.36" in HTML
    # Critical Settings DOM remains present and the v0.8.3 information-architecture
    # initializer must still run. The rejected 0.8.4 accidentally dropped this call.
    assert 'id="settingsAdvancedContent"' in HTML
    assert 'id="preMinRoi"' in HTML
    assert 'id="ipMinRoi"' in HTML
    assert 'id="preMatchBfStart"' in HTML
    assert 'id="preMatchMbStart"' in HTML
    assert 'id="inPlayBfStart"' in HTML
    assert 'id="inPlayMbStart"' in HTML
    assert 'id="bfEnabled"' in HTML
    assert 'id="mbEnabled"' in HTML
    assert 'prepareInformationArchitecture();' in HTML


def test_dashboard_primary_metrics_and_portfolios_follow_agreed_order():
    pipeline = HTML.index('id="dashboardLiveActivity"')
    primary = HTML.index('id="dashboardFinancialPrimary"')
    portfolios = HTML.index('class="dashboard-domain-grid"')
    charts = HTML.index('id="dashboardSportsTrends"')
    assert pipeline < primary < portfolios < charts
    for label in ("Active positions", "Capital in play", "7 day profit", "Today profit"):
        assert f">{label}<" in HTML
    assert '<h2>Sports Portfolio</h2>' in HTML
    assert '<h2>Greyhound Portfolio</h2>' in HTML
    assert 'id="dash7DayProfit"' in HTML


def test_last_completed_scan_progress_is_static_and_cumulative_counts_remain():
    assert 'function renderLastScanProgress(pipe)' in HTML
    assert "classList.add('reached')" in HTML
    assert "classList.add('execution-reached')" in HTML
    assert '.live-connector.reached{background:#3b82f6!important}' in HTML
    assert '.live-stage.execution-reached' in HTML
    assert 'last completed state remains shown' in HTML
    assert 'function loadDashboardTodayPipeline()' in HTML
    assert 'localMidnight(new Date()).toISOString()' in HTML


def test_compact_header_world_clocks_and_theme_support_remain():
    assert 'class="dashboard-header-tools"' in HTML
    assert 'class="analog-clocks"' in HTML
    assert 'id="dashClockUtcHour"' in HTML
    assert 'id="dashClockNewYorkHour"' in HTML
    assert 'id="dashClockSydneyHour"' in HTML
    assert "'America/New_York'" in HTML
    assert "'Australia/Sydney'" in HTML
    assert 'id="themeToggle"' in HTML
    assert 'autobuys.theme' in HTML


def test_sports_monitor_navigation_is_compact_and_analytics_owns_history_scenarios():
    nav = HTML.split('<div class="nav" id="nav"', 1)[1].split('<section id="dashboard"', 1)[0]
    assert 'data-tab="monitor" data-nav-child="sports"' in nav
    assert 'data-tab="matched" data-nav-child="sports"' not in nav
    assert 'data-tab="executions" data-nav-child="sports"' not in nav
    assert 'data-tab="activebets"' in nav
    assert 'data-tab="analytics"' in nav
    assert 'data-tab="replay"' not in nav
    assert 'data-tab="settings"' in nav


def test_no_database_or_trading_reset_is_triggered_by_dashboard_startup():
    # The dashboard refresh path is read-only. Reset remains explicit, guarded by confirm.
    startup = HTML.split("window.addEventListener('pywebviewready'", 1)[1]
    assert 'resetTradingData()' not in startup
    assert 'reset_monitor_balances' not in startup
    assert "callReadBounded('dashboard_overview'" in HTML
    assert "callReadBounded('dashboard_trends'" in HTML
