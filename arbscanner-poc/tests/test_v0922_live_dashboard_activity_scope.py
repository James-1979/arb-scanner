from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_release_version_and_activity_scope_copy():
    from arbscanner import __version__
    assert __version__ == "0.9.39"
    assert "ArbScanner PoC 0.9.39" in HTML
    assert "discovery, matching and processing are shared market activity" in HTML
    assert "opportunities onward follow the selected SIM/LIVE context" in HTML


def test_live_dashboard_activity_uses_shared_prefix_and_live_owned_suffix():
    assert "function renderLiveDashboardActivity0922" in HTML
    assert "fetched:Number(d.fetched||0),matched:Number(d.matched||0),processed:Number(p.processed||0)" in HTML
    assert "opportunities:Number(s.positive||0),qualified:Number(s.qualified||0),executed:Number(m.positions||0)" in HTML
    assert "liveDecisionRead('all','dashboard',{...bounds,include_latest:false,include_rows:false,limit:1})" in HTML
    assert "callReadBounded('live_execution_activity'" in HTML


def test_generic_runtime_refresh_cannot_leave_sim_pipeline_on_live_dashboard():
    assert "const __renderOperationalStatus0922=renderOperationalStatus" in HTML
    assert "if(dataContextMode==='live'&&activePageId()==='dashboard')" in HTML
    assert "renderLiveDashboardActivity0922" in HTML
    assert "primeDashboardActivity0922('live')" in HTML


def test_live_activity_refresh_and_manual_refresh_are_mode_owned():
    assert "startLiveDashboardActivityTimer0922" in HTML
    assert "dataContextMode==='live'&&$('dashboard')?.classList.contains('active')" in HTML
    assert "if(dataContextMode!=='live')return __manualDashboardRefresh0922" in HTML
    assert "let r=await loadLiveDashboard()" in HTML


def test_live_decision_reader_honours_bounds_and_shape_options():
    assert "liveDecisionRead=async function(domain='all',pageId=activePageId(),options={})" in HTML
    assert "payload={domain,limit:300,...(options||{})}" in HTML
    assert "from_utc" in HTML and "to_utc" in HTML
