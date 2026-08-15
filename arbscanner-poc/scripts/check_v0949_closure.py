from pathlib import Path
from playwright.sync_api import sync_playwright
import json, sys

ROOT=Path(__file__).resolve().parents[1]
html=(ROOT/'frontend/index.html').read_text()
stub="""<script>window.pywebview={api:new Proxy({}, {get:()=>async()=>({ok:false,message:'0949 audit stub'})})};</script>"""
html=html.replace('<head>','<head>'+stub,1)
shots=ROOT/'ui-audit-0949';shots.mkdir(exist_ok=True)
checks={}


def activate(page,page_id,pane=None):
    page.evaluate("""([pageId,pane])=>{document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));let p=document.getElementById(pageId);if(p){p.hidden=false;p.classList.add('active')}if(pane){document.querySelectorAll('.analytics-pane').forEach(x=>x.classList.remove('active'));let q=document.querySelector(`.analytics-pane[data-analytics-pane="${pane}"]`);if(q)q.classList.add('active')}}""",[page_id,pane])
    page.wait_for_timeout(80)

with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1568,'height':1050})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content(html,wait_until='load');page.wait_for_timeout(250)

    activate(page,'settings')
    page.evaluate("()=>{upgradeAdmin0948();upgradeAdminTabs0949()}")
    admin=page.evaluate("""()=>({buttons:[...document.querySelectorAll('#adminTabs0949 button')].map(x=>x.textContent.trim()),visible:[...document.querySelectorAll('#adminStack0948>.admin-section0948')].filter(x=>x.getClientRects().length).map(x=>x.querySelector('h2')?.textContent.trim())})""")
    page.click('#adminTabs0949 [data-admin-tab="adminProviders0948"]');page.wait_for_timeout(40)
    admin_after=page.evaluate("""()=>({visible:[...document.querySelectorAll('#adminStack0948>.admin-section0948')].filter(x=>x.getClientRects().length).map(x=>x.querySelector('h2')?.textContent.trim()),active:document.querySelector('#adminTabs0949 button.active')?.textContent.trim()})""")
    checks['admin_tabs']=len(admin['buttons'])==7 and admin['visible']==['System & Safety'] and admin_after['visible']==['Providers & Connections'] and admin_after['active']=='Providers & Connections'
    page.screenshot(path=str(shots/'admin-tabs.png'),full_page=True)

    activate(page,'analytics','performance');page.evaluate("()=>showAnalyticsPane('performance')");page.wait_for_timeout(100)
    perf=page.evaluate("""()=>({venueQuick:!!document.getElementById('performanceVenueQuick0940'),venueValue:document.getElementById('performanceVenue')?.value||null})""")
    checks['performance_venue_removed']=not perf['venueQuick'] and perf['venueValue']=='all'
    page.screenshot(path=str(shots/'performance.png'),full_page=True)

    activate(page,'analytics','replay');page.evaluate("()=>showAnalyticsPane('replay')");page.wait_for_timeout(100)
    replay=page.evaluate("""()=>{let canvas=document.getElementById('timelineReplayCanvas')?.getBoundingClientRect(),layout=document.querySelector('.replay-main-layout0939')?.getBoundingClientRect(),strip=document.getElementById('replayControlStrip0949'),head=document.querySelector('.replay-timeline-head');return {running:!!document.querySelector('.timeline-running-pnl0928'),side:!!document.querySelector('.replay-playback-console0939'),stripInHead:strip?.parentElement===head,canvasH:canvas?.height||0,canvasW:canvas?.width||0,layoutW:layout?.width||0,speeds:document.querySelectorAll('#replaySpeed0940 button').length,clock:!!strip?.querySelector('.timeline-replay-time0937'),play:!!strip?.querySelector('#timelineReplayPlay')}}""")
    checks['replay_duplicate_pnl_removed']=not replay['running'] and not replay['side']
    checks['replay_controls_relocated']=replay['stripInHead'] and replay['speeds']==5 and replay['clock'] and replay['play']
    checks['replay_timeline_full_width']=replay['canvasH']>=188 and replay['layoutW']>0 and replay['canvasW']/replay['layoutW']>.97
    page.screenshot(path=str(shots/'replay.png'),full_page=True)

    checks['page_js_errors']=errors
    browser.close()

print(json.dumps({'checks':checks,'admin':admin,'admin_after':admin_after,'performance':perf,'replay':replay},indent=2))
failed=[k for k,v in checks.items() if (k=='page_js_errors' and v) or (k!='page_js_errors' and not v)]
if failed:
    print('FAILED: '+', '.join(failed),file=sys.stderr);sys.exit(1)
