from pathlib import Path
from playwright.sync_api import sync_playwright
import json, sys

ROOT=Path(__file__).resolve().parents[1]
html=(ROOT/'frontend/index.html').read_text()
stub="""<script>window.pywebview={api:new Proxy({}, {get:()=>async()=>({ok:false,message:'0948 audit stub'})})};</script>"""
html=html.replace('<head>','<head>'+stub,1)
shots=ROOT/'ui-audit-0948';shots.mkdir(exist_ok=True)
checks={}

def activate(page,page_id,pane=None):
    page.evaluate("""([pageId,pane])=>{document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));let p=document.getElementById(pageId);if(p){p.hidden=false;p.classList.add('active')}if(pane){document.querySelectorAll('.analytics-pane').forEach(x=>x.classList.remove('active'));let q=document.querySelector(`.analytics-pane[data-analytics-pane="${pane}"]`);if(q)q.classList.add('active')}}""",[page_id,pane])
    page.wait_for_timeout(80)

with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1568,'height':1050})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content(html,wait_until='load');page.wait_for_timeout(200)

    # Admin canonical surface / tiled settings.
    activate(page,'settings')
    page.evaluate("""()=>{upgradeAdmin0948();renderAdminTechnical0948({settings:{config:{engine_max_concurrent_runtimes:42,price_scan_cache_limit:2500,settlement_poll_seconds:17,alert_retry_minutes:9,snapshot_legacy_keep_rows:111000,snapshot_prune_batch_rows:1200,snapshot_maintenance_seconds:11,matched_market_retention_hours:72,matched_market_prune_batch_rows:7000,matched_market_heartbeat_seconds:800,matched_market_maintenance_seconds:44,live_decision_evidence_enabled:true,live_decision_max_quote_age_seconds:6,live_decision_max_receipt_spread_ms:700,live_decision_min_mapping_confidence:.9}}});renderAdminSystem0948()}""")
    admin=page.evaluate("""()=>({sections:[...document.querySelectorAll('#adminStack0948>.admin-section0948>.admin-section-head0948 h2')].map(x=>x.textContent.trim()),tiles:document.querySelectorAll('#adminStack0948 .admin-field-tile0948').length,technical:document.getElementById('adminEngineConcurrency0948')?.value,topSave:!!document.querySelector('#settings .workspace-head .viewactions'),legacyStrategyVisible:[...document.querySelectorAll('#research1x2,#research2way,#qualityBankroll')].some(x=>x.getClientRects().length>0),bodyW:document.body.scrollWidth,vw:innerWidth})""")
    checks['admin_order']=admin['sections'][:7]==['System & Safety','Providers & Connections','Accounts & Funding','Market Data & Scanner','Alerts','Storage & Maintenance','Technical Settings']
    checks['admin_tiled']=admin['tiles']>=20 and admin['technical']=='42' and not admin['topSave'] and not admin['legacyStrategyVisible']
    checks['admin_no_horizontal_scroll']=admin['bodyW']<=admin['vw']+1
    page.screenshot(path=str(shots/'admin.png'),full_page=True)

    # Performance: Exposure-only chart + real pointer drag.
    activate(page,'analytics','performance')
    page.evaluate("()=>showAnalyticsPane('performance')");page.wait_for_timeout(60)
    page.evaluate(r"""()=>{let rows=Array.from({length:7},(_,i)=>({date:`2026-08-${String(5+i).padStart(2,'0')}T00:00:00+00:00`,label:`${5+i} Aug`,bucket_kind:'day',profit:i===5?-4:i*3,cumulative_period_profit:i*9,capital:8000+i*9,available:7800+i*7,exposure:200+i*2,capital_in_use:200+i*2,settled:i+1,deployed_turnover:80+i*10,portfolio_roi_pct:i*.12,return_on_deployed_pct:i*.8,captured_edge_pct:65+i*3,utilization_pct:2.5+i*.1}));performanceResponse0844={ok:true,timeline_granularity:'day',range_label:'05 Aug → 11 Aug · UTC',summary:{net_pnl:54,portfolio_roi_pct:.68,deployed_turnover:780,current_capital:8054,current_available:7844,current_exposure:210,current_deployed:210,average_deployed:84,peak_deployed:220,average_utilization_pct:2.61,settled_bets:13},rows,capital_timeline:rows.map(x=>({timestamp:x.date,capital:x.capital,available:x.available,capital_in_use:x.capital_in_use,exposure:x.exposure})),performance:{domains:[],markets:[],venues:[],venue_pairs:[],directional_pairs:[],funnel:{},capital_efficiency:{},recovery:{}},venue_capital:{current:{}}};renderPerformanceFinance0931(performanceResponse0844,null);updatePerformanceCopy0948();upgradePerformanceFilters0948();installPerformanceDrag0948();performanceSeekFraction0947(.1)}""")
    box=page.locator('#performanceCapitalTimeline0931').bounding_box();
    x1=box['x']+box['width']*.18;y=box['y']+box['height']*.5;x2=box['x']+box['width']*.78
    before=page.evaluate("""()=>({left:parseFloat(document.getElementById('performanceSharedPlayhead0935')?.style.left||'0'),time:document.getElementById('performanceInspectorTime0937')?.textContent||''})""")
    page.mouse.move(x1,y);page.mouse.down();page.mouse.move(x2,y,steps=8);page.mouse.up();page.wait_for_timeout(50)
    after=page.evaluate("""()=>({left:parseFloat(document.getElementById('performanceSharedPlayhead0935')?.style.left||'0'),time:document.getElementById('performanceInspectorTime0937')?.textContent||'',title:document.querySelector('#performanceCapitalTimeline0931')?.closest('.performance-timeline-panel0931')?.querySelector('.performance-timeline-head0931 strong')?.textContent||'',available:document.querySelectorAll('#performanceCapitalTimeline0931 .available-path').length,capital:document.querySelectorAll('#performanceCapitalTimeline0931 .capital-path').length,exposure:document.querySelectorAll('#performanceCapitalTimeline0931 .capital-exposure-line0948').length,buttons:[...document.querySelectorAll('#performanceButtons0948 button')].filter(x=>x.getClientRects().length).length,cursor:getComputedStyle(document.getElementById('performanceCapitalTimeline0931')).cursor})""")
    checks['performance_drag']=after['left']>before['left']+50 and after['time']!=before['time'] and after['cursor']=='grab'
    checks['performance_exposure_only']=after['title']=='Capital Exposure' and after['available']==0 and after['capital']==0 and after['exposure']==1
    checks['performance_button_filters']=after['buttons']>=4
    page.screenshot(path=str(shots/'performance.png'),full_page=True)

    # Market Analysis: full heatmap hydration + local sport/stream buttons.
    activate(page,'analytics','market')
    page.evaluate("()=>showAnalyticsPane('market')");page.wait_for_timeout(60)
    page.evaluate(r"""()=>{let cells=[];let start=new Date('2026-08-10T00:00:00Z');for(let d=0;d<7;d++)for(let h=0;h<24;h++){let dt=new Date(start.getTime()+d*86400000+h*3600000);cells.push({date:dt.toISOString().slice(0,10),day_index:d,day_label:['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d],hour:h,observed:(d===1&&h===14),observations:(d===1&&h===14)?9:0,unique_markets:(d===1&&h===14)?3:0,net_positive:(d===1&&h===14)?2:0,qualified:(d===1&&h===14)?1:0,executed:0,settled:0,deployed:0,pnl:0,roi_pct:0,available_depth:0,top_book_depth:0,avg_executable_stake:0,liquidity_capable:0,liquidity_rejected:0,liquidity_rejection_rate_pct:0})}marketHeatmapPayload0842={cells,by_sport:{Football:cells.map(x=>({...x,observations:x.observations}))},sports:['Football'],application_mode:'sim'};marketHeatmapSport0948='all';marketHeatmapStream0948='pre_match';applyHeatmapSport0948();renderHeatmapControls0948();renderMarketWeekHeatmap();upgradeMarketFilters0948()}""")
    market=page.evaluate("""()=>({cells:document.querySelectorAll('#marketActivityHours .market-week-cell').length,values:[...document.querySelectorAll('#marketActivityHours .market-week-cell')].map(x=>x.textContent.trim()).filter(x=>x&&!['—','-'].includes(x)),sports:document.querySelectorAll('#heatmapSports0948 button').length,streams:document.querySelectorAll('#heatmapLocalControls0948 .heatmap-control-row0948:nth-child(2) button').length,topButtons:[...document.querySelectorAll('#marketButtons0948 button')].filter(x=>x.getClientRects().length).length,venueVisible:document.getElementById('marketVenueSummary')?.getClientRects().length||0,sportSelectVisible:document.getElementById('marketHeatmapSport')?.getClientRects().length||0})""")
    checks['market_heatmap_data']=market['cells']>=168 and any(v=='9' for v in market['values'])
    checks['market_local_buttons']=market['sports']==2 and market['streams']==3 and market['topButtons']>=4 and market['venueVisible']==0 and market['sportSelectVisible']==0
    page.screenshot(path=str(shots/'market-analysis.png'),full_page=True)

    # Replay: direct drag + cursor-owned headline/engine/sport tiles.
    activate(page,'analytics','replay')
    page.evaluate("()=>showAnalyticsPane('replay')");page.wait_for_timeout(60)
    page.evaluate(r"""()=>{let b=timelineReplayBounds(),span=b.to-b.from,at=f=>new Date(b.from.getTime()+span*f),shape=(emergency=false)=>({emergency,scaled:[],base:[],balance:[],all:[],planned:2,placed:2,recovery:emergency?1:0});timelineReplayRange=b;timelineReplayPositions=[{id:1,event_name:'Alpha v Beta',market_name:'Match Winner',sport:'Football',section:'sports',stream:'pre_match',start:at(.10),settledAt:at(.22),deployed:100,returned:105,pnl:5,settled:true,structure:shape(),row:{outcome:'Beta',engine_instance_id:'SPORTS_BASELINE_ARB_PRIMARY',engine_nickname:'Baseline',engine_provenance_source:'execution_origin'}},{id:2,event_name:'Gamma v Delta',market_name:'Match Odds',sport:'Football',section:'sports',stream:'in_play',start:at(.30),settledAt:at(.52),deployed:120,returned:118,pnl:-2,settled:true,structure:shape(true),row:{outcome:'Delta',engine_instance_id:'SPORTS_SUPERBET_ARB_PRIMARY',engine_nickname:'SuperBet',engine_provenance_source:'execution_origin'}},{id:3,event_name:'Romford R5',market_name:'Win',sport:'Greyhounds',section:'racing',stream:'racing',start:at(.58),settledAt:at(.70),deployed:80,returned:88,pnl:8,settled:true,structure:shape(),row:{outcome:'Trap 4',engine_instance_id:'GREYHOUNDS_BASELINE_ARB_PRIMARY',engine_nickname:'Greyhounds Base',engine_provenance_source:'execution_origin'}}];timelineReplayPeriodActivity0842={engines:[{engine_id:'SPORTS_BASELINE_ARB_PRIMARY',engine:'Baseline',authoritative:true,positions:1,wins:1,losses:0,pnl:5},{engine_id:'SPORTS_SUPERBET_ARB_PRIMARY',engine:'SuperBet',authoritative:true,positions:1,wins:0,losses:1,pnl:-2},{engine_id:'GREYHOUNDS_BASELINE_ARB_PRIMARY',engine:'Greyhounds Base',authoritative:true,positions:1,wins:1,losses:0,pnl:8}]};replayActivityEngine0942='all';replayActivitySport0948='all';renderTimelineReplay();upgradeReplayActivity0948();renderReplayActivityTiles0842();upgradeReplayFilters0948();installReplayDrag0948();timelineReplayUpdateAt(.12,false)}""")
    rbox=page.locator('#timelineReplayCanvas').bounding_box();ry=rbox['y']+rbox['height']*.5;rx1=rbox['x']+rbox['width']*.17;rx2=rbox['x']+rbox['width']*.76
    rb=page.evaluate("""()=>({p:timelineReplayProgress,positions:document.getElementById('timelineReplayPositions')?.textContent,pnl:document.getElementById('timelineReplayProfit')?.textContent})""")
    page.mouse.move(rx1,ry);page.mouse.down();page.mouse.move(rx2,ry,steps=10);page.mouse.up();page.wait_for_timeout(50)
    ra=page.evaluate("""()=>({p:timelineReplayProgress,positions:document.getElementById('timelineReplayPositions')?.textContent,pnl:document.getElementById('timelineReplayProfit')?.textContent,sports:document.querySelectorAll('#timelineReplaySportsTiles0948 .replay-activity-tile').length,engines:document.querySelectorAll('#timelineReplaySportTiles .replay-activity-tile').length,topButtons:[...document.querySelectorAll('#replayButtons0948 button')].filter(x=>x.getClientRects().length).length,oldStreams:document.querySelector('.replay-stream-grid')?.getClientRects().length||0,cursor:getComputedStyle(document.getElementById('timelineReplayCanvas')).cursor,speed:document.querySelectorAll('#replaySpeed0940 button').length})""")
    checks['replay_drag']=ra['p']>rb['p']+.3 and ra['positions']!=rb['positions'] and ra['cursor']=='grab'
    checks['replay_dynamic_tiles']=ra['sports']>=3 and ra['engines']>=4 and ra['pnl']!=rb['pnl']
    checks['replay_filters_and_speed']=ra['topButtons']>=4 and ra['oldStreams']==0 and ra['speed']==5
    page.screenshot(path=str(shots/'replay.png'),full_page=True)

    checks['page_js_errors']=errors
    browser.close()

print(json.dumps({'checks':checks,'admin':admin,'performance_before':before,'performance_after':after,'market':market,'replay_before':rb,'replay_after':ra},indent=2))
failed=[k for k,v in checks.items() if (k=='page_js_errors' and v) or (k!='page_js_errors' and not v)]
if failed:
    print('FAILED: '+', '.join(failed),file=sys.stderr);sys.exit(1)
