from pathlib import Path
from playwright.sync_api import sync_playwright
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
stub = """<script>
window.pywebview={api:new Proxy({}, {get:(t,n)=>async(...args)=>({ok:false,message:'stage12.2 audit stub'})})};
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
      // Performance stale values.
      perfNetPnl.textContent='£999.99';perfCurrentCapitalTop.textContent='£1234.00';
      performanceCapitalTimeline0931.innerHTML='<div id="stalePerf">OLD PERFORMANCE</div>';
      performanceVenueEconomicsBody.innerHTML='<tr id="stalePerfVenue"><td>OLD VENUE</td></tr>';
      primePerformanceShellStage122('live');
      let performance={pnl:perfNetPnl.textContent,capital:perfCurrentCapitalTop.textContent,stale:!!document.getElementById('stalePerf'),venue:performanceVenueEconomicsBody.innerText,chart:performanceCapitalTimeline0931.innerText};

      // Sports Overview stale values.
      sportsOverviewEquity.textContent='£888.00';sportsOverviewMatched.textContent='999';
      sportsMarketHighlights0935.innerHTML='<div id="staleSports">OLD SPORTS</div>';
      sportsOpenPositions.innerHTML='<div id="staleSportsPos">OLD POSITION</div>';
      primeSportsOverviewShellStage122('live');
      let sports={capital:sportsOverviewEquity.textContent,matched:sportsOverviewMatched.textContent,stale:!!document.getElementById('staleSports'),stalePos:!!document.getElementById('staleSportsPos'),highlights:sportsMarketHighlights0935.innerText,positions:sportsOpenPositions.innerText};

      // Racing Overview stale values.
      racingOverviewCapital0941.textContent='£777.00';racingOverviewMatching0941.textContent='321';
      racingHighlights0941.innerHTML='<div id="staleRacing">OLD RACING</div>';
      racingOpenPositions.innerHTML='<div id="staleRacingPos">OLD RACING POSITION</div>';
      primeRacingOverviewShellStage122('live');
      let racing={capital:racingOverviewCapital0941.textContent,matching:racingOverviewMatching0941.textContent,stale:!!document.getElementById('staleRacing'),stalePos:!!document.getElementById('staleRacingPos'),highlights:racingHighlights0941.innerText,positions:racingOpenPositions.innerText};
      return {performance,sports,racing};
    }""")
    details["surfaces"] = result
    checks["performance_clears_synchronously"] = result["performance"]["pnl"] == "—" and result["performance"]["capital"] == "—" and not result["performance"]["stale"] and "Loading LIVE" in result["performance"]["chart"] and "Loading LIVE" in result["performance"]["venue"]
    checks["sports_overview_clears_synchronously"] = result["sports"]["capital"] == "—" and result["sports"]["matched"] == "—" and not result["sports"]["stale"] and not result["sports"]["stalePos"] and "Loading LIVE Sports" in result["sports"]["highlights"] and "Loading LIVE Sports" in result["sports"]["positions"]
    checks["racing_overview_clears_synchronously"] = result["racing"]["capital"] == "—" and result["racing"]["matching"] == "—" and not result["racing"]["stale"] and not result["racing"]["stalePos"] and "Loading LIVE Racing" in result["racing"]["highlights"] and "Loading LIVE Racing" in result["racing"]["positions"]
    checks["page_js_errors"] = errors
    browser.close()
print(json.dumps({"checks": checks, "details": details}, indent=2))
failed = [k for k, v in checks.items() if (k == "page_js_errors" and v) or (k != "page_js_errors" and not v)]
if failed:
    print("FAILED: " + ", ".join(failed), file=sys.stderr)
    sys.exit(1)
