from __future__ import annotations

from pathlib import Path

from arbscanner import __version__

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
RELEASE_NOTES = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")
INSTALLER = (ROOT / "BUILD_AND_INSTALL.command").read_text(encoding="utf-8")


def _v0946_script() -> str:
    return HTML.split('<script id="v0946-ui-consistency-js">', 1)[1].split("</script>", 1)[0]


def _v0946_css() -> str:
    return HTML.split('<style id="v0946-ui-consistency-css">', 1)[1].split("</style>", 1)[0]


def test_0946_release_identity_and_notes():
    assert __version__ == "0.9.46"
    assert "<title>ArbScanner PoC 0.9.46</title>" in HTML
    assert 'EXPECTED_VERSION="0.9.46"' in INSTALLER
    assert "## 0.9.46 — UI Control Consistency & Racing Engine Parity" in RELEASE_NOTES


def test_0946_common_choice_button_component_replaces_segmented_visual_grammar():
    css = _v0946_css()
    js = _v0946_script()

    # One reusable component owns period/filter/toggle presentation. This includes
    # the old segmented hosts, whose grey trough is explicitly removed.
    assert ".choice-button-group0946>button" in css
    assert "border-radius:8px!important" in css
    assert "background:#eaf2ff!important" in css
    assert "border-color:#84adff!important" in css
    assert ".choice-button-group0946.segmented" in css
    assert "background:transparent!important" in css
    assert "border:0!important" in css
    for selector in (
        ".segmented",
        ".period-buttons0941",
        ".sport-toggle-row0941",
        ".engine-period-buttons0942",
        ".venue-quick-filter0940",
        ".timeline-speed-buttons0940",
        ".position-filter-row",
        ".execution-action-toggles",
    ):
        assert selector in js
    assert "classList.add('choice-button-group0946')" in js


def test_0946_monitor_executed_status_is_green_in_sports_and_racing():
    js = _v0946_script()
    assert "function applyExecutedMonitorStatus0946" in js
    assert "trim().toUpperCase()==='EXECUTED'" in js
    assert "tag.classList.add('good')" in js

    # Both canonical Monitor renderers re-apply the status rule after each render.
    assert "applyExecutedMonitorStatus0946($('monitorRows'))" in js
    assert "applyExecutedMonitorStatus0946($('racingMonitorRows'))" in js


def test_0946_sports_and_racing_engines_share_one_table_and_summary_renderer():
    js = _v0946_script()
    assert "function engineLifecycleSummaryHtml0946" in js
    assert "function engineLifecycleTableHtml0946" in js
    assert "renderSportsEngines0936=function()" in js
    assert "renderRacingEngines0941=function()" in js
    assert "engineLifecycleTableHtml0946('sports',rows)" in js
    assert "engineLifecycleTableHtml0946('racing',rows)" in js

    expected_headers = (
        "<th>Engine</th><th>State</th><th>Stream</th><th>Processed</th>"
        "<th>Opportunities</th><th>Qualified</th><th>Executed</th><th>Settled</th>"
        "<th>P&amp;L</th><th>Errors</th><th>Last Activity</th><th>Enabled</th>"
    )
    assert expected_headers in js

    # The legacy class is a grid-row component and must not be attached to a real
    # <tr>. That was the concrete source of Racing table visual divergence.
    table_block = js.split("function engineLifecycleTableHtml0946", 1)[1].split(
        "function filteredEngineRows0946", 1
    )[0]
    assert '<tr class="engine-row0914' not in table_block
    assert "section==='racing'?'Pre-race'" in js


def test_0946_racing_engines_has_sports_style_safety_notice_and_solid_drawer_structure():
    racing = HTML.split('<section id="racing-engines"', 1)[1].split("</section>", 1)[0]
    js = _v0946_script()
    css = _v0946_css()

    assert "Safety invariant" in racing
    assert "provider credentials or order-write APIs" in racing
    assert 'id="racingEngineDrawerNav0946" class="engine-drawer-nav0938"' in racing
    assert 'class="engine-drawer0938 racing-engine-drawer0941"' in racing
    assert "openRacingEngineDrawer0941=async function" in js
    for action in ("Export Engine", "View in Monitor", "View Results", "Model in Scenarios"):
        assert action in js
    assert "Racing Config owns the outer guardrails" in js
    assert "#racingEngineDetail0914" in css
