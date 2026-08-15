from pathlib import Path
from playwright.sync_api import sync_playwright
import json, sys

ROOT=Path(__file__).resolve().parents[1]
html=(ROOT/'frontend/index.html').read_text(encoding='utf-8')
stub="""<script>window.pywebview={api:new Proxy({}, {get:()=>async()=>({ok:false,message:'0953 audit stub'})})};</script>"""
html=html.replace('<head>','<head>'+stub,1)
checks={}; details={}

with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1568,'height':1050})
    errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content(html,wait_until='load'); page.wait_for_timeout(250)

    replay=page.evaluate("""()=>{
      document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));let a=document.getElementById('analytics');a.hidden=false;a.classList.add('active');document.querySelectorAll('.analytics-pane').forEach(x=>x.classList.remove('active'));document.querySelector('.analytics-pane[data-analytics-pane="replay"]').classList.add('active');
      dataContextMode='sim';let now=new Date('2026-08-14T12:00:00Z');
      timelineReplayPositions=[
        {id:1,start:new Date('2026-08-14T10:00:00Z'),settledAt:new Date('2026-08-14T11:00:00Z'),settled:true,pnl:2,deployed:20,structure:{emergency:false,scaled:[]},stream:'pre_match',sport:'Football',event_name:'Real Engine Position',market_name:'Match Odds',row:{engine_instance_id:'sports-core-1',engine_nickname:'Sports Core',engine_provenance_source:'execution_origin',mode:'sim'}},
        {id:2,start:new Date('2026-08-14T10:30:00Z'),settledAt:new Date('2026-08-14T11:30:00Z'),settled:true,pnl:-1,deployed:10,structure:{emergency:false,scaled:[]},stream:'pre_match',sport:'Football',event_name:'Historical Position',market_name:'Match Odds',row:{engine_nickname:'Legacy / Unverified',engine_provenance_source:'legacy',mode:'sim'}}
      ];
      timelineReplayPeriodActivity0842={engines:[{engine_id:'sports-core-1',engine:'Sports Core',authoritative:true,positions:1,pnl:2},{engine_id:'legacy_unverified',engine:'Legacy / Unverified',authoritative:false,positions:1,pnl:-1}]};
      timelineReplayRange={from:new Date('2026-08-14T00:00:00Z'),to:new Date('2026-08-15T00:00:00Z')};
      replayActivityEngine0942='all';setReplayEngineOptions0953();renderReplayActivityTiles0842(now);renderReplayLedger0917();
      let tiles=[...document.querySelectorAll('#timelineReplaySportTiles [data-replay-engine]')].map(x=>({id:x.dataset.replayEngine,text:x.textContent.trim()}));
      let opts=[...document.querySelectorAll('#timelineReplayEngine0917 option')].map(x=>({value:x.value,text:x.textContent.trim()}));
      let ledger=document.getElementById('timelineReplayRows0917')?.textContent||'';
      replayActivitySelectEngine0942('sports-core-1');
      let filtered=timelineReplayFilteredPositions().map(x=>x.id);
      return {tiles,opts,ledger,filtered};
    }""")
    details['replay']=replay
    checks['replay_only_real_engine_tiles']=[x['id'] for x in replay['tiles']]==['all','sports-core-1'] and all('Legacy / Unverified' not in x['text'] for x in replay['tiles'])
    checks['replay_engine_select_real_ids']=[x['value'] for x in replay['opts']]==['all','sports-core-1']
    checks['replay_legacy_not_engine_label']='Legacy / Unverified' not in replay['ledger'] and 'No stored Engine provenance' in replay['ledger']
    checks['replay_real_engine_filters_by_id']=replay['filtered']==[1]

    market=page.evaluate("""async()=>{
      document.querySelectorAll('.analytics-pane').forEach(x=>x.classList.remove('active'));document.querySelector('.analytics-pane[data-analytics-pane="market"]').classList.add('active');
      currentAnalyticsPane=()=> 'market';modeRequestCurrent=()=>true;dataContextMode='sim';document.getElementById('marketAnalysisScope').value='all';
      let calls=[];
      callReadBounded=async(method,args)=>{let payload=JSON.parse(JSON.stringify(args?.[0]||{}));calls.push({method,payload});if(method==='market_analysis')return {ok:true,rows:[{section:'sports',sport:'Football',market_name:'Match Odds',in_play:0,observations:8,net_positive:3,qualified:1,attempts:1,executed:1,settled:1}],reasons:[],liquidity_funnel:{observed:8,positive:3,liquidity_capable:2,qualified:1,attempted:1,executed:1,settled:1},venue_summary:[],latest_racing_discovery:{summary:{}},racing_discovery:{},summary_history_complete:true,detailed_history_complete:true};return {ok:true,cells:[],by_sport:{},sports:['Football'],metrics:['observations'],metric_ownership:{observations:'shared'},application_mode:'sim'};};
      marketHeatmapCache0835.clear();marketHeatmapStreams0952=new Set(marketHeatmapStreamOrder0952);
      await loadMarketAnalysis();let allCalls=calls.splice(0);
      marketHeatmapStreams0952=new Set(['pre_match','racing']);await loadMarketAnalysis();let partialCalls=calls.splice(0);
      document.getElementById('marketAnalysisScope').value='sports';await loadMarketHeatmapDay0835(true);let scopedCalls=calls.splice(0);
      return {allCalls,partialCalls,scopedCalls,observed:document.getElementById('marketObserved')?.textContent};
    }""")
    details['market']=market
    def find(calls,name):
        return next((x for x in calls if x['method']==name),None)
    all_main=find(market['allCalls'],'market_analysis'); all_heat=find(market['allCalls'],'market_heatmap')
    part_main=find(market['partialCalls'],'market_analysis'); part_heat=find(market['partialCalls'],'market_heatmap')
    scoped_heat=find(market['scopedCalls'],'market_heatmap')
    checks['market_all_uses_legacy_unfiltered_route']=bool(all_main and all_heat) and 'streams' not in all_main['payload'] and 'streams' not in all_heat['payload']
    checks['market_partial_streams_reach_main_and_heatmap']=part_main['payload'].get('streams')=='pre_match,racing' and part_heat['payload'].get('streams')=='pre_match,racing'
    checks['market_portfolio_scope_reaches_heatmap']=scoped_heat['payload'].get('scope')=='sports'
    checks['market_main_data_renders']=market['observed']=='8'
    checks['page_js_errors']=errors
    browser.close()

print(json.dumps({'checks':checks,'details':details},indent=2))
failed=[k for k,v in checks.items() if (k=='page_js_errors' and v) or (k!='page_js_errors' and not v)]
if failed:
    print('FAILED: '+', '.join(failed),file=sys.stderr);sys.exit(1)
