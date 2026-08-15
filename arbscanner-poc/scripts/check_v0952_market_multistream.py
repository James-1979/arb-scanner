from pathlib import Path
from playwright.sync_api import sync_playwright
import json, sys

ROOT=Path(__file__).resolve().parents[1]
html=(ROOT/'frontend/index.html').read_text(encoding='utf-8')
stub="""<script>window.pywebview={api:new Proxy({}, {get:()=>async()=>({ok:false,message:'0952 audit stub'})})};</script>"""
html=html.replace('<head>','<head>'+stub,1)
checks={}; details={}

with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1568,'height':1050})
    errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content(html,wait_until='load'); page.wait_for_timeout(250)
    result=page.evaluate("""async()=>{
      document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));
      let a=document.getElementById('analytics');a.hidden=false;a.classList.add('active');
      document.querySelectorAll('.analytics-pane').forEach(x=>x.classList.remove('active'));
      document.querySelector('.analytics-pane[data-analytics-pane="market"]').classList.add('active');
      modeRequestCurrent=()=>true;currentAnalyticsPane=()=> 'market';dataContextMode='sim';
      let calls=[];
      callReadBounded=async(method,args)=>{calls.push({method,payload:JSON.parse(JSON.stringify(args?.[0]||{}))});return {ok:true,cells:[],by_sport:{},sports:['Football','Tennis','Greyhounds'],metrics:['observations'],metric_ownership:{observations:'shared'},application_mode:'sim'}};
      marketHeatmapCache0835.clear();marketHeatmapStreams0952=new Set(marketHeatmapStreamOrder0952);renderHeatmapControls0948();
      const state=()=>({all:[...document.querySelectorAll('#heatmapStreams0952 button')].map(b=>({text:b.textContent.trim(),active:b.classList.contains('active'),pressed:b.getAttribute('aria-pressed')})),streams:heatmapStreams0952()});
      let initial=state();
      await setHeatmapStream0948('pre_match');await new Promise(r=>setTimeout(r,10));let afterPre=state();
      await setHeatmapStream0948('in_play');await new Promise(r=>setTimeout(r,10));let afterIn=state();
      let beforeLast=calls.length;await setHeatmapStream0948('racing');await new Promise(r=>setTimeout(r,10));let afterLast=state(),lastBlocked=calls.length===beforeLast;
      await setHeatmapStream0948('all');await new Promise(r=>setTimeout(r,10));let afterAll=state();
      dataContextMode='live';await loadMarketHeatmapDay0835(true);
      return {initial,afterPre,afterIn,afterLast,afterAll,lastBlocked,calls};
    }""")
    details=result
    checks['initial_all_selected']=result['initial']['streams']==['pre_match','in_play','racing'] and all(x['active'] for x in result['initial']['all'])
    checks['multi_toggle']=result['afterPre']['streams']==['in_play','racing'] and result['afterIn']['streams']==['racing']
    checks['last_stream_protected']=result['afterLast']['streams']==['racing'] and result['lastBlocked']
    checks['all_restores_all']=result['afterAll']['streams']==['pre_match','in_play','racing']
    routed=[x for x in result['calls'] if x['method'] in ('market_heatmap','live_market_heatmap')]
    checks['backend_receives_stream_arrays']=bool(routed) and all(isinstance(x['payload'].get('streams'),list) and x['payload'].get('scope')=='all' and x['payload'].get('phase')=='all' for x in routed)
    checks['live_uses_same_route']=routed[-1]['method']=='live_market_heatmap' and routed[-1]['payload'].get('streams')==['pre_match','in_play','racing']
    checks['page_js_errors']=errors
    browser.close()

print(json.dumps({'checks':checks,'details':details},indent=2))
failed=[k for k,v in checks.items() if (k=='page_js_errors' and v) or (k!='page_js_errors' and not v)]
if failed:
    print('FAILED: '+', '.join(failed),file=sys.stderr);sys.exit(1)
