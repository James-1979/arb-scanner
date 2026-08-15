from pathlib import Path
from playwright.sync_api import sync_playwright
import json, sys

ROOT=Path(__file__).resolve().parents[1]
html=(ROOT/'frontend/index.html').read_text(encoding='utf-8')
stub="""<script>
window.__calls=[];
window.pywebview={api:new Proxy({}, {get:(t,n)=>async(...args)=>{window.__calls.push({name:String(n),args});return {ok:false,message:'0957 audit stub'}}})};
</script>"""
html=html.replace('<head>','<head>'+stub,1)
checks={};details={}
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1568,'height':959})
    errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content(html,wait_until='load'); page.wait_for_timeout(250)
    result=page.evaluate("""async()=>{
      document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));
      document.getElementById('dashboard').classList.add('active');
      dataContextMode='sim';syncDataModeControls();
      document.getElementById('dashBankroll').textContent='£1,234.56';
      document.getElementById('dashBestWinMeta').textContent='SIM best win retained';
      document.getElementById('trendPnlChart').innerHTML='<div id="simTrendMarker">SIM trend retained</div>';
      let before={bank:document.getElementById('dashBankroll').textContent,best:document.getElementById('dashBestWinMeta').textContent,trend:document.getElementById('trendPnlChart').innerText,calls:window.__calls.length};
      clearLiveDashboardEconomicState();
      let afterClear={bank:document.getElementById('dashBankroll').textContent,best:document.getElementById('dashBestWinMeta').textContent,trend:document.getElementById('trendPnlChart').innerText,calls:window.__calls.length};
      let liveResult=await loadLiveDashboard();
      let afterLoad={bank:document.getElementById('dashBankroll').textContent,best:document.getElementById('dashBestWinMeta').textContent,trend:document.getElementById('trendPnlChart').innerText,calls:window.__calls.length};
      dashboardVenueMetrics0920={
        betfair:{capital:700,capital_in_play:40,profit_today:12.5,locked_profit:2.5,bankroll_share_pct:50,share_drift_pct_points:1,net_capital_migration:10},
        matchbook:{capital:700,capital_in_play:30,profit_today:-5,locked_profit:1.5,bankroll_share_pct:50,share_drift_pct_points:-1,net_capital_migration:-10},
        smarkets:{capital:null,capital_in_play:0,profit_today:0,locked_profit:0}
      };
      renderDashboardVenueAccounts0920({accounts:{
        betfair:{display_name:'Betfair',exchange:'betfair',currency:'GBP',equity:700,exposure:40,freshness:'CURRENT'},
        matchbook:{display_name:'Matchbook',exchange:'matchbook',currency:'GBP',equity:700,exposure:30,freshness:'CURRENT'}
      }},dashboardVenueMetrics0920,'sim');
      let totals={capital:document.getElementById('dashTotalCapital0921').textContent,inplay:document.getElementById('dashTotalCapitalInPlay0921').textContent,profit:document.getElementById('dashTotalProfitToday0921').textContent,locked:document.getElementById('dashTotalLockedProfit0921').textContent,accounts:document.getElementById('dashboardExchangeAccounts').innerText};
      return {before,afterClear,liveResult,afterLoad,totals};
    }""")
    details['mode_guard']=result
    checks['sim_clear_is_noop']=result['before']==result['afterClear']
    checks['sim_live_loader_is_noop']=result['liveResult'].get('stale_context') is True and result['afterLoad']['calls']==result['before']['calls'] and result['afterLoad']['bank']==result['before']['bank']
    checks['dashboard_financial_render_still_works']='£1,400.00' in result['totals']['capital'] and '£70.00' in result['totals']['inplay'] and '+£7.50' in result['totals']['profit'] and '+£4.00' in result['totals']['locked'] and 'SETTLEMENT CONTRIBUTION TODAY' in result['totals']['accounts']
    checks['page_js_errors']=errors
    browser.close()
print(json.dumps({'checks':checks,'details':details},indent=2))
failed=[k for k,v in checks.items() if (k=='page_js_errors' and v) or (k!='page_js_errors' and not v)]
if failed:
    print('FAILED: '+', '.join(failed),file=sys.stderr);sys.exit(1)
