from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def _body_after(marker: str, end_marker: str) -> str:
    return HTML.split(marker, 1)[1].split(end_marker, 1)[0]


def test_route_orchestrator_primes_heavy_page_shell_before_loader_dispatch():
    body = _body_after("function orchestrateRouteLoad", "function setGlobalDataMode")
    assert "primeRouteShellStage122(id)" in body
    assert body.index("primeRouteShellStage122(id)") < body.index("let loader=routeLoadersStage03")


def test_mode_switch_primes_current_heavy_route_before_queued_reload():
    body = _body_after("function primeModeOwnedRouteShellStage03", "const routeLoadersStage03")
    assert "primeRouteShellStage122(activePageId())" in body
    init = _body_after("function initialiseDataModeShell", "async function loadLiveDashboard")
    assert "primeModeOwnedRouteShellStage03()" in init
    mode = _body_after("function setGlobalDataMode", "// Override account rendering")
    changed = mode.split("if(next===dataContextMode)", 1)[1]
    assert "primeModeOwnedRouteShellStage03()" in changed
    assert changed.index("primeModeOwnedRouteShellStage03()") < changed.index("queueMicrotask(()=>orchestrateRouteLoad(activePageId()))")


def test_performance_shell_is_cleared_before_pane_load_and_direct_refresh():
    pane = _body_after("function showAnalyticsPane", "function currentAnalyticsPane")
    assert "primePerformanceShellStage122(dataContextMode)" in pane
    assert pane.index("primePerformanceShellStage122") < pane.index("loadPerformanceAnalytics()")
    loader = HTML.rsplit("loadPerformanceAnalytics=async function(){performanceBasis='actual'", 1)[1].split("loadLivePerformance=async function", 1)[0]
    assert "primePerformanceShellStage122(mode)" in loader
    assert loader.index("primePerformanceShellStage122(mode)") < loader.index("Promise.all")


def test_sports_overview_shell_clears_finance_activity_and_position_surfaces():
    body = _body_after("function primeSportsOverviewShellStage122", "function primeRacingOverviewShellStage122")
    for token in (
        "sportsOverviewEquity",
        "sportsOverviewAvailable",
        "sportsOverviewCommitted",
        "sportsOverviewToday",
        "sportsOverviewMatched",
        "sportsOverviewQualified",
        "sportsMarketHighlights0935",
        "sportsOpenPositions",
    ):
        assert token in body
    loader = _body_after("async function loadSports0935", "loadSports=async function")
    assert "primeSportsOverviewShellStage122(mode)" in loader
    assert loader.index("primeSportsOverviewShellStage122(mode)") < loader.index("callReadBounded('sports_overview'")


def test_racing_overview_shell_clears_finance_status_highlights_and_positions():
    body = _body_after("function primeRacingOverviewShellStage122", "function primeRouteShellStage122")
    for token in (
        "racingOverviewCapital0941",
        "racingOverviewAvailable0941",
        "racingOverviewDeployed0941",
        "racingOverviewPnl0941",
        "racingOverviewMatching0941",
        "racingOverviewPriceState0941",
        "racingOverviewDiscoveryState0941",
        "racingHighlights0941",
        "racingOpenPositions",
    ):
        assert token in body
    loader = _body_after("async function loadRacing0941", "loadRacing=loadRacing0941")
    assert "primeRacingOverviewShellStage122(mode)" in loader
    assert loader.index("primeRacingOverviewShellStage122(mode)") < loader.index("callReadBounded('racing_overview'")


def test_stage122_does_not_change_backend_or_execution_surface():
    assert "stage122" not in (ROOT / "arbscanner" / "api.py").read_text(encoding="utf-8").lower()
    assert "stage122" not in (ROOT / "arbscanner" / "db.py").read_text(encoding="utf-8").lower()
