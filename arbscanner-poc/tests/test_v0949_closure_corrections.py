from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
DB_SOURCE = (ROOT / "arbscanner" / "db.py").read_text(encoding="utf-8")
API_SOURCE = (ROOT / "arbscanner" / "api.py").read_text(encoding="utf-8")
INSTALLER = (ROOT / "BUILD_AND_INSTALL.command").read_text(encoding="utf-8")
NOTES = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")


def _js() -> str:
    return HTML.split('<script id="v0949-closure-js">', 1)[1].split("</script>", 1)[0]


def _css() -> str:
    return HTML.split('<style id="v0949-closure-css">', 1)[1].split("</style>", 1)[0]


def test_0949_release_identity_and_installer():
    assert __version__ == "0.9.49"
    assert "<title>ArbScanner PoC 0.9.49</title>" in HTML
    assert 'EXPECTED_VERSION="0.9.49"' in INSTALLER
    assert "## 0.9.49 — Analytics Layout & Admin Tabs Closure" in NOTES


def test_0949_performance_venue_quick_buttons_are_removed_and_scope_normalised():
    js = _js()
    css = _css()
    assert "#performanceVenueQuick0940{display:none!important}" in css
    assert "function removePerformanceVenueQuick0949" in js
    assert "if(select&&select.value!=='all')select.value='all'" in js
    assert "if(host)host.remove()" in js
    # Historical controls may remain in the compatibility layer, but the final
    # 0.9.49 layer owns their removal.
    assert "syncPerformanceVenueQuick0940=function(){removePerformanceVenueQuick0949()}" in js


def test_0949_heatmap_uses_authoritative_bounded_financial_ledger(tmp_path: Path):
    api = API(tmp_path / "heatmap.sqlite3")
    db = api.db
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    finish = start + timedelta(days=7)

    # A stale historical compact row deliberately disagrees with the canonical
    # ledger result. 0.9.49 must return the canonical value instead.
    with db.lock:
        db.conn.execute(
            """INSERT OR REPLACE INTO market_financial_hourly_rollups(
                 hour_utc,section,sport,market_name,in_play,qualified,executed,deployed,settled,settled_deployed,pnl
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (start.isoformat(), "sports", "Football", "Match Odds", 0, 1, 1, 10.0, 1, 10.0, 0.25),
        )
        db.conn.commit()

    db.ensure_market_hourly_rollups = lambda *_a, **_k: None
    db.ensure_liquidity_opportunity_rollups = lambda *_a, **_k: None
    db._financial_hour_rows_between = lambda *_a, **_k: [{
        "hour_utc": start.isoformat(), "section": "sports", "sport": "Football",
        "market_name": "Match Odds", "in_play": 0, "qualified": 4, "executed": 3,
        "deployed": 125.0, "settled": 2, "settled_deployed": 100.0, "pnl": 12.5,
    }]

    payload = db.market_heatmap_between(start.isoformat(), finish.isoformat())
    assert payload["financial_source"] == "authoritative_sim_ledger"
    assert payload["financial"][0]["pnl"] == 12.5
    assert payload["financial"][0]["settled"] == 2
    assert payload["financial"][0]["executed"] == 3
    assert payload["source"] == "compact_market_rollups+authoritative_sim_ledger"


def test_0949_heatmap_api_exposes_authoritative_financial_source(tmp_path: Path):
    api = API(tmp_path / "heatmap-api.sqlite3")
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    finish = start + timedelta(days=7)
    api.db.market_heatmap_between = lambda *_a, **_k: {
        "source": "compact_market_rollups+authoritative_sim_ledger",
        "financial_source": "authoritative_sim_ledger",
        "rollups": [],
        "financial": [{
            "hour_utc": start.isoformat(), "section": "sports", "sport": "Football",
            "market_name": "Match Odds", "in_play": 0, "qualified": 2, "executed": 2,
            "deployed": 60.0, "settled": 1, "settled_deployed": 30.0, "pnl": 6.75,
        }],
        "liquidity_depth": [], "liquidity_opportunity": [],
    }
    result = api.market_heatmap({
        "from_utc": start.isoformat(), "to_utc": finish.isoformat(),
        "timezone_name": "UTC", "timezone_offset_minutes": 0,
    })
    assert result["ok"] is True
    assert result["financial_source"] == "authoritative_sim_ledger"
    assert sum(float(x["pnl"]) for x in result["cells"]) == 6.75
    assert sum(int(x["settled"]) for x in result["cells"]) == 1


def test_0949_admin_is_top_tabbed_with_seven_owned_sections():
    js = _js()
    css = _css()
    for label in (
        "System & Safety", "Providers & Connections", "Accounts & Funding",
        "Market Data & Scanner", "Alerts", "Storage & Maintenance", "Technical Settings",
    ):
        assert label in js
    assert "const adminTabs0949=[" in js
    assert "role','tablist'" in js
    assert "setAdminTab0949" in js
    assert "section.hidden=!active" in js
    assert "#adminStack0948>.admin-section0948[hidden]{display:none!important}" in css


def test_0949_replay_removes_duplicate_running_pnl_and_uses_full_width_timeline():
    js = _js()
    css = _css()
    assert "let pnl=document.querySelector('.timeline-running-pnl0928')" in js
    assert "pnl.remove();block?.remove()" in js
    assert "replayControlStrip0949" in js
    assert "head?.appendChild(strip)" in js
    assert "strip.appendChild(actions)" in js
    assert "panel?.remove()" in js
    assert ".replay-main-layout0939{grid-template-columns:minmax(0,1fr)!important" in css
    assert "grid-template-rows:190px!important" in css
    assert "height:190px!important" in css
    assert ".replay-control-strip0949" in css


def test_0949_live_order_writes_remain_locked(tmp_path: Path):
    api = API(tmp_path / "lock.sqlite3")
    state = api.get_state()
    assert state["settings"]["live_execution_available"] is False
    assert all(feed["live_execution_effective"] is False for feed in state["operations"]["feeds"])
    assert '"live_order_writes": False' in API_SOURCE


def test_0949_no_db_migration_or_provider_expansion_in_closure_delta():
    assert "No DB migration/reset is required" in NOTES.split("## 0.9.48", 1)[0]
    assert "No provider integration expansion" in NOTES.split("## 0.9.48", 1)[0]
    assert "CREATE TABLE" not in DB_SOURCE.split("def market_heatmap_between", 1)[1].split("def latest_matched_markets", 1)[0]
