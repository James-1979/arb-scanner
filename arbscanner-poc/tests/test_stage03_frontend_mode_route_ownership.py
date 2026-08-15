from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_route_generation_is_part_of_every_mode_request_token():
    assert "var modeEpoch=0,routeEpoch=0" in HTML
    assert "route_epoch:routeEpoch" in HTML
    assert "Number(token.route_epoch)!==routeEpoch" in HTML
    assert "token.pane!==currentAnalyticsPane()" in HTML


def test_route_loader_ownership_is_explicit_for_both_modes():
    assert "const routeLoadersStage03={" in HTML
    assert "sim:{dashboard:()=>loadDashboardOverview()" in HTML
    assert "live:{dashboard:()=>loadLiveDashboard()" in HTML
    assert "activebets:()=>loadLiveActivePositions()" in HTML
    assert "monitor:()=>loadLiveMonitor('sports')" in HTML
    assert "'racing-monitor':()=>loadLiveRacingMonitor()" in HTML
    assert "analytics:()=>showAnalyticsPane(currentAnalyticsPane?.()||'performance',true)" in HTML
    assert "let loader=routeLoadersStage03[dataContextMode]?.[id]" in HTML


def test_route_and_analytics_changes_invalidate_previous_responses():
    route_body = HTML.split("function orchestrateRouteLoad", 1)[1].split("function setGlobalDataMode", 1)[0]
    analytics_body = HTML.split("function showAnalyticsPane", 1)[1].split("function currentAnalyticsPane", 1)[0]
    assert "routeEpoch+=1" in route_body
    assert "if(!reuseRouteEpoch)routeEpoch+=1" in analytics_body
    assert "showAnalyticsPane(currentAnalyticsPane?.()||'performance',true)" in HTML


def test_mode_switch_primes_owned_shell_before_queued_route_load():
    body = HTML.split("function setGlobalDataMode", 1)[1].split("// Override account rendering", 1)[0]
    assert "modeEpoch+=1" in body
    assert "primeFinancialShellForMode();primeModeOwnedRouteShellStage03();" in body
    assert "queueMicrotask(()=>orchestrateRouteLoad(activePageId()))" in body
    assert body.index("primeModeOwnedRouteShellStage03()") < body.index("queueMicrotask(()=>orchestrateRouteLoad(activePageId()))", body.index("modeEpoch+=1"))


def test_historical_component_alias_uses_owning_page_for_stale_gate():
    assert "const routePageAliasesStage03={'monitor-last-detected':'monitor'}" in HTML
    assert "function routeOwnedPageStage03" in HTML
    assert "let page=routeOwnedPageStage03(activePageId())" in HTML


def test_old_098_mode_switch_wrapper_is_retired_but_active_positions_guard_remains():
    assert "__setGlobalDataMode098" not in HTML
    assert "__initialiseDataModeShell098" not in HTML
    assert "__setGlobalDataMode0944" in HTML
    assert "primeActivePositionsShell0944('live')" in HTML
