from pathlib import Path
from playwright.sync_api import sync_playwright
import json, sys

ROOT=Path(__file__).resolve().parents[1]
html=(ROOT/'frontend/index.html').read_text(encoding='utf-8')
stub="""<script>window.pywebview={api:new Proxy({}, {get:()=>async()=>({ok:false,message:'0951 audit stub'})})};</script>"""
html=html.replace('<head>','<head>'+stub,1)
checks={};details={}

with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1568,'height':1050})
    errors=[]; page.on('pageerror',lambda e: errors.append(str(e)))
    page.set_content(html,wait_until='load'); page.wait_for_timeout(250)
    page.evaluate("""()=>{document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));document.getElementById('analytics').classList.add('active');document.querySelectorAll('.analytics-pane').forEach(x=>x.classList.remove('active'));document.querySelector('[data-analytics-pane="scenarios"]').classList.add('active')}""")
    result=page.evaluate("""async()=>{
      let calls=[];
      modeRequestCurrent=()=>true;
      call=async(method,payload={})=>{
        calls.push({method,payload});
        if(method==='scenario_capital_sources')return {ok:true,sim_accounts:{betfair:500,matchbook:450,smarkets:0},budgets:{pre_match:{hedge_reserve:25,hedge_reserve_pct:5,venues:{betfair:{normal_deployable:200,equity:225},matchbook:{normal_deployable:200,equity:225}}},in_play:{venues:{}},racing:{venues:{}}}};
        if(method==='engines')return {ok:true,rows:[{engine_instance_id:'SPORTS_BASELINE_ARB_PRIMARY',display_name:'Baseline ARB',nickname:'Baseline',engine_grade:'PRODUCTION',effective_lifecycle:'ACTIVE',section:'sports',capabilities:[]},{engine_instance_id:'GREYHOUNDS_BASELINE_ARB_PRIMARY',display_name:'Greyhounds Base',engine_grade:'PRODUCTION',effective_lifecycle:'ACTIVE',section:'racing',capabilities:[]}]};
        if(method==='analytics_replay')return {ok:true,result:{starting_capital:950,ending_capital:960,realized_profit:10,realized_roi_pct:1.0526,total_deployed:100,return_on_deployed_pct:10,locked_profit:9,locked_return_on_deployed_pct:9,max_drawdown_pct:0.2,peak_concurrent_deployed:100,peak_capital_tied_pct:10.526,exchange_balances:{starting:{betfair:500,matchbook:450,smarkets:0},ending:{betfair:506,matchbook:454,smarkets:0}},series:[],counts:{settled_available:1,taken:1},events:[{id:42,engine_instance_id:'SPORTS_BASELINE_ARB_PRIMARY',engine_type:'baseline_arb',quality_band:'Strong',sport:'Football',event_name:'Alpha v Beta',market_name:'Match Odds',outcome:'Home',exchange_stakes:{betfair:50,matchbook:50},capital_before:950,deployed:100,locked_profit:9,locked_return_pct:9,realized_pnl:10,capital_after_result:960}]},evidence_comparison:{monitor:{ending_capital:960,profit:10,opportunities:1},actual:{}},actual_performance:{available:true,ending_capital:960,profit:10,deployed:100,settled:1,basis_matches_scenario:true},stream_comparison:{pre_match:{taken:1,settled_available:1,locked_profit:9,locked_return_on_deployed_pct:9,realized_profit:10,return_on_deployed_pct:10,peak_concurrent_deployed:100},in_play:{},racing:{taken:0,settled_available:0,realized_profit:0},combined:{taken:1,settled_available:1,locked_profit:9,locked_return_on_deployed_pct:9,realized_profit:10,return_on_deployed_pct:10,peak_concurrent_deployed:100}},comparison:[],scenario_diagnostics:{scenario_total_ms:3}};
        if(method==='engine_scenario_compare')return {ok:true,market_snapshot_id:'snap',input_observed_at:'2026-08-14T18:00:00Z',rows:[]};
        return {ok:false,message:'unhandled '+method};
      };
      dataContextMode='sim';scenarioCapitalSourcesCache=null;scenarioEngineCatalog0951=[];scenarioEngineCatalogPromise0951=null;
      await loadScenarioContext0951(true);
      let origin=document.getElementById('scenarioOriginEngine0951');origin.value='SPORTS_BASELINE_ARB_PRIMARY';await loadReplay();
      let analyticsCalls=calls.filter(x=>x.method==='analytics_replay');
      let bounds={};for(let period of ['yesterday','this_week','this_month']){document.getElementById('replayPeriod').value=period;let b=replayBounds(false);bounds[period]={from:b.from_utc,to:b.to_utc}}
      let sim={contentHidden:document.getElementById('scenarioSimContent0951').hidden,noticeHidden:document.getElementById('scenarioLiveNotice0951').hidden,originOptions:[...origin.options].map(o=>o.value),racingText:document.getElementById('replayRacingMeta').textContent,rowText:document.getElementById('scenarioResultsRows').textContent,modelButton:!!document.querySelector('#scenarioResultsRows button[data-engine="SPORTS_BASELINE_ARB_PRIMARY"]'),lastPayload:analyticsCalls.at(-1)?.payload,bounds};
      let beforeLive=analyticsCalls.length;dataContextMode='live';await loadScenarioContext0951();let afterLive=calls.filter(x=>x.method==='analytics_replay').length;
      let live={contentHidden:document.getElementById('scenarioSimContent0951').hidden,noticeHidden:document.getElementById('scenarioLiveNotice0951').hidden,analyticsCallsBefore:beforeLive,analyticsCallsAfter:afterLive};
      return {calls,sim,live};
    }""")
    details=result
    sim=result['sim']; live=result['live']; payload=sim['lastPayload'] or {}
    checks['single_engine_catalog_populates_history_filter']='SPORTS_BASELINE_ARB_PRIMARY' in sim['originOptions'] and 'GREYHOUNDS_BASELINE_ARB_PRIMARY' in sim['originOptions']
    checks['engine_filter_routes_to_analytics']=payload.get('engine_instance_id')=='SPORTS_BASELINE_ARB_PRIMARY' and payload.get('strategy')=='all'
    checks['transaction_engine_handoff']=sim['modelButton'] and 'Baseline' in sim['rowText']
    checks['racing_stream_rendered']='selected' in sim['racingText']
    checks['calendar_presets_resolve']=all(v['from'] and v['to'] for v in sim['bounds'].values())
    checks['sim_shell_visible']=not sim['contentHidden'] and sim['noticeHidden']
    checks['live_hides_all_sim_scenario_economics']=live['contentHidden'] and not live['noticeHidden'] and live['analyticsCallsAfter']==live['analyticsCallsBefore']
    checks['page_js_errors']=errors
    browser.close()

print(json.dumps({'checks':checks,'details':details},indent=2,default=str))
failed=[k for k,v in checks.items() if (k=='page_js_errors' and v) or (k!='page_js_errors' and not v)]
if failed:
    print('FAILED: '+', '.join(failed),file=sys.stderr);sys.exit(1)
