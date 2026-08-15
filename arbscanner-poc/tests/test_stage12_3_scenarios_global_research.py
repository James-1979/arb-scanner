from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def _body_after(marker: str, end_marker: str) -> str:
    return HTML.split(marker, 1)[1].split(end_marker, 1)[0]


def test_scenario_console_is_explicitly_global_and_visible_in_both_modes():
    assert 'id="scenarioGlobalNotice0955"' in HTML
    assert 'GLOBAL research:' in HTML
    assert 'shared identically across SIM and LIVE application modes' in HTML
    assert 'no SIM scenario result is projected into LIVE' not in HTML
    shell = _body_after("function setScenarioModeShell0951", "async function loadScenarioContext0951")
    assert "content.hidden=false" in shell
    assert "notice.hidden=false" in shell
    assert "dataContextMode" not in shell


def test_scenario_context_and_run_have_no_live_mode_blank_branch():
    context = _body_after("async function loadScenarioContext0951", "async function refreshScenario0951")
    assert "loadLiveScenarioEmpty" not in context
    assert "scenarioGlobalRouteToken0955" in context
    assert "scenarioGlobalRouteCurrent0955" in context
    replay = _body_after("async function loadReplay()", "async function saveModeRules")
    assert "loadLiveScenarioEmpty" not in replay
    assert "modeRequestToken('sim'" not in replay
    assert "dataContextMode!=='sim'" not in replay
    assert "scenarioGlobalRouteToken0955" in replay
    assert "scenarioGlobalRouteCurrent0955" in replay


def test_scenario_capital_sources_remain_sim_research_evidence_but_are_mode_independent():
    body = _body_after("async function loadScenarioCapitalSources", "function scenarioCapitalSourceChanged")
    assert "scenario_capital_sources" in body
    assert "dataContextMode!=='sim'" not in body
    assert "modeRequestToken('sim'" not in body
    assert "scenarioGlobalRouteToken0955" in body
    assert "scenarioGlobalRouteCurrent0955" in body


def test_mode_switch_preserves_global_scenario_without_route_reload_or_clear():
    body = _body_after("function setGlobalDataMode", "// Override account rendering")
    assert "let preserveGlobalScenario=scenarioGlobalActive0955()" in body
    assert "if(preserveGlobalScenario){setScenarioModeShell0951();return Promise.resolve({ok:true,mode:next,scenario_global:true})}" in body
    preserve_index = body.index("if(preserveGlobalScenario)")
    # Shared shell priming may run for generic mode bookkeeping, but Scenarios must
    # return before the queued route reload that would invalidate/reload its state.
    assert "primeFinancialShellForMode();primeModeOwnedRouteShellStage03();" in body
    assert preserve_index < body.index("queueMicrotask(()=>orchestrateRouteLoad(activePageId()))", preserve_index)


def test_scenario_badge_is_global_research_in_both_application_modes():
    body = _body_after("function syncModeContextVisibility0927", "const routePageAliasesStage03")
    assert "GLOBAL · RESEARCH" in body
    assert "currentAnalyticsPane?.()==='scenarios'" in body
    assert "badge.dataset.mode=pageCopy===globalScenario?'global':dataContextMode" in body


def test_scenario_engine_launch_runs_the_same_global_model_from_live_or_sim():
    body = _body_after("async function openScenarioWithEngine0951", "function openScenarioEngineForOpportunity0951")
    assert "return loadReplay()" in body
    assert "dataContextMode" not in body


def test_stage123_is_frontend_only():
    for path in (ROOT / "arbscanner" / "api.py", ROOT / "arbscanner" / "db.py"):
        text = path.read_text(encoding="utf-8")
        assert "stage123" not in text.lower()
        assert "scenario_global" not in text
