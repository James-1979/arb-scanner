from pathlib import Path
from playwright.sync_api import sync_playwright
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
stub = """<script>
window.__scenarioCalls=[];
window.pywebview={api:new Proxy({}, {get:(t,n)=>async(...args)=>{
  window.__scenarioCalls.push({name:n,args:args});
  if(n==='set_data_context_mode') return {ok:true,mode:args?.[0]?.mode||'sim'};
  if(n==='analytics_replay') return {ok:true,result:{starting_capital:12057.74,ending_capital:12178.3294,realized_profit:120.5894,realized_roi_pct:1.0,peak_concurrent_deployed:489.93,peak_capital_tied_pct:4.1,max_drawdown_pct:1.97,total_deployed:11983.46,counts:{taken:72,settled_available:296},series:[{time:'2026-08-15T12:00:00Z',bankroll:12057.74,exposure:0},{time:'2026-08-15T13:00:00Z',bankroll:12100.0,exposure:100},{time:'2026-08-15T14:00:00Z',bankroll:12178.3294,exposure:0}]},scenario_diagnostics:{scenario_total_ms:291}};
  if(n==='scenario_capital_sources') return {ok:true,sim_accounts:{betfair:{equity:6000},matchbook:{equity:6057.74}}};
  if(n==='engines') return {ok:true,rows:[]};
  return {ok:true};
}})};
</script>"""
html = html.replace("<head>", "<head>" + stub, 1)
checks = {}
details = {}
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, executable_path="/usr/bin/chromium", args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1568, "height": 959})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_content(html, wait_until="load")
    page.wait_for_timeout(300)
    result = page.evaluate("""async()=>{
      document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));
      document.getElementById('analytics').classList.add('active');
      document.querySelectorAll('[data-analytics-pane]').forEach(x=>x.classList.toggle('active',x.dataset.analyticsPane==='scenarios'));
      document.querySelectorAll('[data-analytics-tab]').forEach(x=>x.classList.toggle('active',x.dataset.analyticsTab==='scenarios'));
      analyticsTitle.textContent='Scenarios';
      modeBootstrapped=true;dataContextMode='sim';modeEpoch=10;routeEpoch=77;
      setScenarioModeShell0951();syncDataModeControls();

      scenarioStartingBalance0954.value='12057.74';
      scenarioHedge0954.value='20';scenarioMaxStake0954.value='25';scenarioMinRoi0954.value='1';
      scenarioDate0954.value='2026-08-15';
      scenarioRunState0954.textContent='Scenario current';scenarioRunState0954.className='good small';
      scenarioEnd0954.textContent='£12,178.3294';scenarioPnl0954.textContent='+£120.5894';scenarioRoi0954.textContent='1.00%';scenarioExecuted0954.textContent='72';
      scenarioRunSummary0954.textContent='GLOBAL FIXTURE · identical state';
      scenarioTimelineChart0954.innerHTML='<svg id="scenarioFixtureChart"><path d="M0 0 L10 10"></path></svg>';
      scenarioLastResult0954={ok:true,result:{ending_capital:12178.3294}};
      scenarioLastPayload0954={starting_capital:12057.74};
      scenarioHydrated0954=true;

      const snapshot=()=>({
        mode:dataContextMode,
        routeEpoch,
        start:scenarioStartingBalance0954.value,
        hedge:scenarioHedge0954.value,
        stake:scenarioMaxStake0954.value,
        roi:scenarioMinRoi0954.value,
        date:scenarioDate0954.value,
        end:scenarioEnd0954.textContent,
        pnl:scenarioPnl0954.textContent,
        executed:scenarioExecuted0954.textContent,
        summary:scenarioRunSummary0954.textContent,
        chart:scenarioTimelineChart0954.innerHTML,
        contentHidden:scenarioSimContent0951.hidden,
        noticeHidden:scenarioGlobalNotice0955.hidden,
        badge:analyticsTitle.querySelector('.mode-context-badge0927')?.textContent||'',
        badgeMode:analyticsTitle.querySelector('.mode-context-badge0927')?.dataset.mode||'',
        lastResult:JSON.stringify(scenarioLastResult0954),
        lastPayload:JSON.stringify(scenarioLastPayload0954)
      });
      let sim=snapshot();
      await setGlobalDataMode('live');
      await new Promise(r=>setTimeout(r,20));
      let live=snapshot();
      await setGlobalDataMode('sim');
      await new Promise(r=>setTimeout(r,20));
      let simAgain=snapshot();

      // Prove a Scenario can also be run while the application is LIVE.
      await setGlobalDataMode('live');
      scenarioCapitalSourcesCache={ok:true,sim_accounts:{betfair:{equity:6000},matchbook:{equity:6057.74}}};
      scenarioEngineCatalog0951=[];
      scenarioHydrated0954=true;
      let beforeCalls=window.__scenarioCalls.filter(x=>x.name==='analytics_replay').length;
      let run=await loadReplay();
      let afterCalls=window.__scenarioCalls.filter(x=>x.name==='analytics_replay').length;
      let liveRun={ok:!!run?.ok,calls:afterCalls-beforeCalls,end:scenarioEnd0954.textContent,executed:scenarioExecuted0954.textContent,badge:analyticsTitle.querySelector('.mode-context-badge0927')?.textContent||''};
      return {sim,live,simAgain,liveRun};
    }""")
    details["scenarios"] = result
    stable_keys = ["routeEpoch","start","hedge","stake","roi","date","end","pnl","executed","summary","chart","contentHidden","noticeHidden","lastResult","lastPayload"]
    checks["sim_to_live_preserves_exact_scenario_state"] = all(result["sim"][k] == result["live"][k] for k in stable_keys)
    checks["live_to_sim_preserves_exact_scenario_state"] = all(result["sim"][k] == result["simAgain"][k] for k in stable_keys)
    checks["global_badge_in_both_modes"] = result["sim"]["badge"] == "GLOBAL · RESEARCH" and result["live"]["badge"] == "GLOBAL · RESEARCH" and result["simAgain"]["badge"] == "GLOBAL · RESEARCH" and result["live"]["badgeMode"] == "global"
    checks["global_content_never_hides"] = not result["sim"]["contentHidden"] and not result["live"]["contentHidden"] and not result["simAgain"]["contentHidden"] and not result["live"]["noticeHidden"]
    checks["mode_switch_does_not_advance_route_epoch"] = result["sim"]["routeEpoch"] == 77 and result["live"]["routeEpoch"] == 77 and result["simAgain"]["routeEpoch"] == 77
    checks["scenario_can_run_while_application_live"] = result["liveRun"]["ok"] and result["liveRun"]["calls"] == 1 and result["liveRun"]["executed"] == "72" and result["liveRun"]["badge"] == "GLOBAL · RESEARCH"
    checks["page_js_errors"] = errors
    browser.close()
print(json.dumps({"checks": checks, "details": details}, indent=2))
failed = [k for k, v in checks.items() if (k == "page_js_errors" and v) or (k != "page_js_errors" and not v)]
if failed:
    print("FAILED: " + ", ".join(failed), file=sys.stderr)
    sys.exit(1)
