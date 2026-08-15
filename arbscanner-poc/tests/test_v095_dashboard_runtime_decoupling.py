from pathlib import Path

from arbscanner import __version__

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _function_body(name: str) -> str:
    marker = f"function {name}("
    start = HTML.find(marker)
    assert start >= 0, f"missing function {name}"
    brace = HTML.find("{", start)
    depth = 0
    quote = None
    escaped = False
    for i in range(brace, len(HTML)):
        ch = HTML[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in {"'", '"', '`'}:
            quote = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return HTML[brace + 1 : i]
    raise AssertionError(f"unterminated function {name}")


def test_v095_identity_and_clock_is_decoupled_from_backend():
    assert __version__ == "0.9.36"
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML
    assert "function startUiRuntimeTimers()" in HTML
    assert "function startDashboardClock()" in HTML
    assert "dashboardClockTimer=setInterval(renderDashboardClock,1000)" in HTML
    startup = HTML.index("startUiRuntimeTimers()", HTML.index("pywebviewready"))
    async_init = HTML.index("initialiseApplicationAsync()", startup)
    assert startup < async_init


def test_v095_clock_renderer_is_dom_only_and_uses_current_date():
    body = _function_body("renderDashboardClock")
    assert "new Date()" in body
    assert "setAnalogClock" in body
    for forbidden in ("call(", "callReadBounded(", "loadDashboard", "loadRacing", "pywebview"):
        assert forbidden not in body
    rollover = _function_body("checkDashboardDayRollover")
    assert "loadDashboardTodayPipeline()" in rollover
    assert "loadDashboardTrends()" in rollover
    assert "loadDashboardPerformance()" in rollover
    assert "loadDashboardOverview()" in rollover


def test_v095_ui_timers_are_idempotent_and_named():
    start_clock = _function_body("startDashboardClock")
    start_countdown = _function_body("startPollCountdown")
    assert "if(dashboardClockTimer)return dashboardClockTimer" in start_clock
    assert "if(pollCountdownTimer)return pollCountdownTimer" in start_countdown
    assert "dashboardLiveActivityTimer" in HTML
    assert "dashboardPipelineTimer" in HTML
    assert "appStateRefreshTimer" in HTML
    assert "dashboardOverviewTimer" in HTML
    assert "visibleLiveAccountsTimer" in HTML


def test_v095_dashboard_reads_are_bounded_without_changing_generic_call():
    generic = _function_body("call")
    start = HTML.index("async function callReadBounded(")
    end = HTML.index("async function awaitUiBounded(", start)
    bounded = HTML[start:end]
    assert "setTimeout" not in generic
    assert "Promise.race" in bounded
    assert "uiReadFlights" in bounded
    assert "modeRequestCurrent" in bounded
    for method in (
        "dashboard_overview",
        "dashboard_trends",
        "pipeline_analytics",
        "racing_overview",
        "settled_positions",
        "dashboard_results_24h",
        "live_activity_status",
    ):
        assert f"callReadBounded('{method}'" in HTML


def test_v095_dashboard_core_does_not_await_optional_panels():
    body = _function_body("loadDashboardOverview")
    assert "callReadBounded('dashboard_overview'" in body
    assert "void launchDashboardOptionalPanels" in body
    assert "await Promise.all([loadDashboardPipeline" not in body
    optional = _function_body("launchDashboardOptionalPanels")
    assert "void runDashboardOptionalPanel('pipeline'" in optional
    assert "void runDashboardOptionalPanel('performance'" in optional
    assert "void runDashboardOptionalPanel('racing'" in optional
    assert "void runDashboardOptionalPanel('trends'" in optional
    assert "void runDashboardOptionalPanel('results'" in optional
    assert "loadRacingMonitor" not in optional


def test_v095_manual_refreshes_always_clear_busy_state():
    dashboard = _function_body("manualDashboardRefresh")
    monitor = _function_body("manualMonitorRefresh")
    assert "finally" in dashboard and "setActionBusy('dashboard',false,'refresh')" in dashboard
    assert "finally" in monitor and "setActionBusy('monitor',false,'refresh')" in monitor


def test_v095_startup_scanner_wait_is_bounded_but_not_treated_as_cancelled():
    body = _function_body("initialiseApplicationAsync")
    assert "call('ensure_scanner_running'" in body
    assert "awaitUiBounded" in body
    assert "startBackendRuntimeTimers()" in body
    assert "run_scan_now" not in body


def test_v095_live_order_safety_boundary_remains_locked():
    assert "LIVE order writing remains centrally locked" in HTML
    assert "function startDashboardClock()" in HTML
    clock = _function_body("startDashboardClock")
    assert "provider" not in clock.lower()
    assert "account" not in clock.lower()
