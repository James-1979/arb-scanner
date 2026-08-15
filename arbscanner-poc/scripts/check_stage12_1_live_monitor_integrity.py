from pathlib import Path
from playwright.sync_api import sync_playwright
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
stub = """<script>
window.pywebview={api:new Proxy({}, {get:(t,n)=>async(...args)=>({ok:false,message:'stage12.1 audit stub'})})};
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
    page.wait_for_timeout(250)
    result = page.evaluate("""()=>{
      document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));
      document.getElementById('monitor').classList.add('active');
      dataContextMode='sim';syncDataModeControls();
      sportsEngineLifecycleRows0936=[{engine_instance_id:'SIM_TEST',processed:16568,opportunities:16094,qualified:80,executed:4}];
      matchedAll=[{section:'sports',event_name:'SIM row',market_name:'Match Winner',sport:'Football',status:'recommended',mode:'sim',venue:'betfair + matchbook',venue_ids:['betfair','matchbook'],net_roi_pct:2.1,engine_provenance_source:'routing_only',engine_instance_id:'SIM_TEST'}];
      activeMonitorPositions=[];renderMonitor0936();
      let sim={processed:monitorProcessed.textContent,opps:monitorPositive.textContent,qualified:monitorQualified.textContent,executed:monitorExecuted.textContent};

      dataContextMode='live';syncDataModeControls();
      // Deliberately re-inject stale SIM lifecycle state to prove LIVE renderer cannot consume it.
      sportsEngineLifecycleRows0936=[{engine_instance_id:'SIM_TEST',processed:16568,opportunities:16094,qualified:80,executed:4}];
      matchedAll=[
        liveMonitorRow0936({event_name:'LIVE A',market_name:'Match Winner',sport:'Football',provider_pair:'betfair + matchbook',net_roi_pct:1.5,max_executable_stake:10}),
        liveMonitorRow0936({event_name:'LIVE B',market_name:'Totals',sport:'Football',provider_pair:'betfair + matchbook',net_roi_pct:-0.3,max_executable_stake:8})
      ];
      activeMonitorPositions=[];renderMonitor0936();
      let live={processed:monitorProcessed.textContent,opps:monitorPositive.textContent,qualified:monitorQualified.textContent,executed:monitorExecuted.textContent,context:monitorQualificationBreakdown.textContent};

      // Route priming must blank the old DOM synchronously before any async loader response.
      monitorExecuted.textContent='4';monitorQualified.textContent='80';monitorRows.innerHTML='<div id="staleSim">STALE SIM</div>';
      primeSportsMonitorModeShellStage121('live');
      let primed={qualified:monitorQualified.textContent,executed:monitorExecuted.textContent,stale:!!document.getElementById('staleSim'),rows:monitorRows.innerText};
      return {sim,live,primed};
    }""")
    details["monitor"] = result
    checks["sim_fixture_reproduces_field_defect"] = result["sim"]["qualified"] == "80" and result["sim"]["executed"] == "4"
    checks["live_ignores_stale_sim_lifecycle"] = result["live"]["processed"] == "2" and result["live"]["opps"] == "1" and result["live"]["qualified"] == "0" and result["live"]["executed"] == "0"
    checks["live_context_is_fail_closed"] = "fail closed" in result["live"]["context"]
    checks["route_prime_clears_stale_dom_synchronously"] = result["primed"]["qualified"] == "0" and result["primed"]["executed"] == "0" and not result["primed"]["stale"] and "Loading LIVE Sports Monitor" in result["primed"]["rows"]
    checks["page_js_errors"] = errors
    browser.close()
print(json.dumps({"checks": checks, "details": details}, indent=2))
failed = [k for k, v in checks.items() if (k == "page_js_errors" and v) or (k != "page_js_errors" and not v)]
if failed:
    print("FAILED: " + ", ".join(failed), file=sys.stderr)
    sys.exit(1)
