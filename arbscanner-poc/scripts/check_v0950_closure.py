from pathlib import Path
from playwright.sync_api import sync_playwright
import json, sys

ROOT=Path(__file__).resolve().parents[1]
html=(ROOT/'frontend/index.html').read_text(encoding='utf-8')
stub="""<script>window.pywebview={api:new Proxy({}, {get:()=>async()=>({ok:false,message:'0950 audit stub'})})};</script>"""
html=html.replace('<head>','<head>'+stub,1)
shots=ROOT/'ui-audit-0950';shots.mkdir(exist_ok=True)
checks={}; details={}

def activate(page,page_id,pane=None):
    page.evaluate("""([pageId,pane])=>{document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));let p=document.getElementById(pageId);if(p){p.hidden=false;p.classList.add('active')}if(pane){document.querySelectorAll('.analytics-pane').forEach(x=>x.classList.remove('active'));let q=document.querySelector(`.analytics-pane[data-analytics-pane="${pane}"]`);if(q)q.classList.add('active')}}""",[page_id,pane])
    page.wait_for_timeout(100)

with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1568,'height':1050})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content(html,wait_until='load');page.wait_for_timeout(300)

    # Performance: right aligned filters and blue Capital Exposure.
    activate(page,'analytics','performance');page.evaluate("()=>showAnalyticsPane('performance')");page.wait_for_timeout(100)
    perf=page.evaluate("""()=>{alignAnalyticsFilters0950();performanceResponse0844={capital_timeline:[{at:'2026-08-14T10:00:00Z',capital_in_use:10},{at:'2026-08-14T11:00:00Z',capital_in_use:25}]};renderCapitalTimeline0931(performanceResponse0844.capital_timeline);let h=document.getElementById('performanceButtons0948'),p=h?.parentElement,hr=h?.getBoundingClientRect(),pr=p?.getBoundingClientRect(),line=document.querySelector('#performanceCapitalTimeline0931 .capital-exposure-line0948'),area=document.querySelector('#performanceCapitalTimeline0931 .capital-exposure-area0948');return {rightGap:hr&&pr?Math.abs(pr.right-hr.right):999,margin:getComputedStyle(h).marginLeft,justify:getComputedStyle(h).justifyContent,stroke:line?getComputedStyle(line).stroke:null,fill:area?getComputedStyle(area).fill:null}}""")
    details['performance']=perf
    checks['performance_filters_right']=perf['rightGap'] < 5 and perf['justify']=='flex-end'
    checks['performance_exposure_blue']=perf['stroke'] in {'rgb(23, 92, 211)','rgba(23, 92, 211, 1)'} and perf['fill'] not in {None,'none'}
    page.screenshot(path=str(shots/'performance.png'),full_page=True)

    # Replay: visible filter buttons own the right edge.
    activate(page,'analytics','replay');page.evaluate("()=>showAnalyticsPane('replay')");page.wait_for_timeout(100)
    replay=page.evaluate("""()=>{alignAnalyticsFilters0950();let h=document.getElementById('replayButtons0948'),p=h?.parentElement,hr=h?.getBoundingClientRect(),pr=p?.getBoundingClientRect();return {rightGap:hr&&pr?Math.abs(pr.right-hr.right):999,margin:getComputedStyle(h).marginLeft,justify:getComputedStyle(h).justifyContent}}""")
    details['replay']=replay; checks['replay_filters_right']=replay['rightGap']<5 and replay['justify']=='flex-end'
    page.screenshot(path=str(shots/'replay.png'),full_page=True)

    # Market Analysis: every local stream routes through the unified bounded heatmap path in SIM and LIVE.
    activate(page,'analytics','market')
    routes=page.evaluate("""async()=>{
      modeRequestCurrent=()=>true;currentAnalyticsPane=()=> 'market';let calls=[];
      callReadBounded=async(method,args,opts)=>{calls.push({method,payload:args?.[0]||{}});return {ok:true,cells:[],by_sport:{},sports:['Football','Greyhounds'],metrics:['observations','qualified','pnl'],metric_ownership:{observations:'shared',qualified:method.startsWith('live')?'live':'sim',pnl:method.startsWith('live')?'live':'sim'},application_mode:method.startsWith('live')?'live':'sim',route_integrity:{rollup_rows:1,financial_rows:1,liquidity_depth_rows:1,liquidity_opportunity_rows:1,cell_count:168,metric_count:16}}};
      marketHeatmapCache0835.clear();dataContextMode='sim';
      marketHeatmapStream0948='pre_match';await loadMarketHeatmapDay0835(true);
      marketHeatmapStream0948='in_play';await loadMarketHeatmapDay0835(true);
      marketHeatmapStream0948='racing';await loadMarketHeatmapDay0835(true);
      dataContextMode='live';marketHeatmapStream0948='in_play';await loadLiveMarketHeatmap099(true);
      return calls;
    }""")
    details['market_routes']=routes
    expected=[('market_heatmap','sports','pre_match'),('market_heatmap','sports','in_play'),('market_heatmap','racing','all'),('live_market_heatmap','sports','in_play')]
    got=[(x['method'],x['payload'].get('scope'),x['payload'].get('phase')) for x in routes]
    checks['market_heatmap_stream_routes']=got==expected

    # Admin: effective values hydrate, Smarkets readiness is explicit, layout uses full width.
    activate(page,'settings');page.evaluate("()=>{upgradeAdmin0948();upgradeAdminTabs0949();setAdminTab0949('adminAccounts0948')}");page.wait_for_timeout(120)
    admin=page.evaluate("""()=>{
      state={...(state||{}),settings:{...((state||{}).settings||{}),config:{account_refresh_seconds:37,discovery_interval_seconds:71,price_scan_tick_seconds:4,alert_min_profit:2.75,alert_min_deployed_roi_pct:1.25,engine_max_concurrent_runtimes:77,snapshot_legacy_keep_rows:123456,live_decision_max_quote_age_seconds:6.5},scenarios:[500,2500]}};
      hydrateAdminValues0950(state);
      renderAccountGrid('adminExchangeAccountGrid',{mode:'sim',accounts:{}});
      let grid=document.getElementById('adminExchangeAccountGrid'),gr=getComputedStyle(grid),sm=grid.querySelector('.admin-smarkets-pending0950');
      return {refresh:document.getElementById('accountRefreshSeconds')?.value,scan:document.getElementById('scanInterval')?.value,tick:document.getElementById('priceTick')?.value,profit:document.getElementById('alertProfit')?.value,droi:document.getElementById('alertDroi')?.value,concurrency:document.getElementById('adminEngineConcurrency0948')?.value,keep:document.getElementById('adminSnapshotKeep0948')?.value,liveAge:document.getElementById('adminLiveQuoteAge0948')?.value,smarkets:!!sm,smarketsText:sm?.textContent||'',columns:gr.gridTemplateColumns};
    }""")
    details['admin']=admin
    checks['admin_values_hydrated']=(admin['refresh'],admin['scan'],admin['tick'],admin['profit'],admin['droi'],admin['concurrency'],admin['keep'],admin['liveAge'])==('37','71','4','2.75','1.25','77','123456','6.5')
    checks['admin_smarkets_readiness']=admin['smarkets'] and 'AWAITING API' in admin['smarketsText'] and 'not integrated' in admin['smarketsText']
    checks['admin_account_three_columns']=len(admin['columns'].split())==3
    page.screenshot(path=str(shots/'admin-accounts.png'),full_page=True)

    page.evaluate("()=>setAdminTab0949('adminStorage0948')");page.wait_for_timeout(80)
    storage=page.evaluate("""()=>{tidyAdminLayout0950();let host=document.querySelector('#adminStorage0948 .admin-section-body0948'),hr=host?.getBoundingClientRect(),cards=[...host?.querySelectorAll(':scope>.card')||[]].map(x=>({title:x.querySelector('h2,h3')?.textContent||'',w:x.getBoundingClientRect().width,wide:x.classList.contains('admin-storage-wide0950')}));return {columns:getComputedStyle(host).gridTemplateColumns,hostW:hr?.width||0,cards}}""")
    details['storage']=storage
    checks['storage_two_column_layout']=len(storage['columns'].split())==2 and storage['hostW']>0
    page.screenshot(path=str(shots/'admin-storage.png'),full_page=True)

    checks['page_js_errors']=errors
    browser.close()

print(json.dumps({'checks':checks,'details':details},indent=2))
failed=[k for k,v in checks.items() if (k=='page_js_errors' and v) or (k!='page_js_errors' and not v)]
if failed:
    print('FAILED: '+', '.join(failed),file=sys.stderr);sys.exit(1)
