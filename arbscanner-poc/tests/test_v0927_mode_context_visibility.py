from pathlib import Path

from arbscanner import __version__

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
INSTALLER = (ROOT / "BUILD_AND_INSTALL.command").read_text()


def test_v0927_release_and_installer_are_locked_together():
    assert __version__ == "0.9.39"
    assert '<title>ArbScanner PoC 0.9.39</title>' in HTML
    assert 'ArbScanner PoC 0.9.39' in HTML
    assert 'EXPECTED_VERSION="0.9.39"' in INSTALLER
    assert 'Extract the 0.9.39 package and run its installer there.' in INSTALLER


def test_v0927_mode_badges_are_explicit_and_do_not_add_a_new_header_row():
    assert '.mode-context-badge0927{display:inline-flex' in HTML
    assert 'height:20px' in HTML
    assert "short:'SIM · VIRTUAL'" in HTML
    assert "short:'LIVE · ACTUAL ONLY'" in HTML
    assert 'Actual live data only; SIM data is not used as fallback.' in HTML
    assert "const modeContextPages0927=new Set(['activebets','monitor','sports','racing','racing-monitor','analytics']);" in HTML
    # Dashboard remains deliberately untouched to preserve its clock/header geometry.
    assert "new Set(['dashboard'" not in HTML


def test_v0927_mode_badges_update_before_route_data_loads():
    start = HTML.index('function syncDataModeControls()')
    end = HTML.index('function initialiseDataModeShell', start)
    body = HTML[start:end]
    assert 'syncModeContextVisibility0927();renderMode()' in body
    assert "$('globalDataModeLive')?.classList.toggle('active',dataContextMode==='live')" in body


def test_v0927_analytics_readds_badge_after_dynamic_title_change():
    start = HTML.index("function showAnalyticsPane(name='performance')")
    end = HTML.index('function currentAnalyticsPane()', start)
    body = HTML[start:end]
    assert "$('analyticsTitle').textContent=m[0]" in body
    assert 'syncModeContextVisibility0927()' in body


def test_v0927_accounts_context_is_more_explicit_without_data_semantic_changes():
    assert 'id="accountsModeBadge">SIM · VIRTUAL</span>' in HTML
    assert "dataContextMode==='live'?'LIVE · ACTUAL ONLY':'SIM · VIRTUAL'" in HTML
    assert 'SIM data is not used as fallback.' in HTML
