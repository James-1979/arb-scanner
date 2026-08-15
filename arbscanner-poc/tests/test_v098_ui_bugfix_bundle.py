from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def _add_settled(api: API, *, event: str, sport: str, section: str, stream: str, signature: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    oid = api.db.add_opportunity(
        signature, event, None, "Winner", 2.0, 2.0, [], [], 1.0, signature,
        sport=sport, section=section, in_play=(stream == "in_play"),
    )
    with api.db.lock:
        api.db.conn.execute(
            """INSERT INTO monitor_positions(opportunity_id,event_key,market_name,opened_at,settled_at,status,deployed,expected_profit,
                     stakes_by_exchange_json,outcome_exchange_pnls_json,simulation_json,stream,currency,outcome,realized_pnl,realized_by_exchange_json)
                     VALUES(?,?,?,?,?,'SETTLED',?,?,?,?,?,?,'GBP',?,?,?)""",
            (oid, signature, "Winner", now, now, 100.0, 2.0, '{}', '{}', '{}', stream, "Winner", 1.0, '{}'),
        )
        api.db.conn.commit()
    return oid


def test_v098_identity_and_live_generic_panel_retired():
    assert __version__ == "0.9.36"
    assert "ArbScanner PoC 0.9.36" in HTML
    ensure = HTML.split("function ensureLiveModePanel", 1)[1].split("function clearAccountsPageModeShell", 1)[0]
    assert "insertAdjacentElement" not in ensure
    assert "document.createElement" not in ensure
    renderer = HTML.split("async function renderLiveModePanel", 1)[1].split("function liveEmptyText", 1)[0]
    assert "live_view_data" not in renderer
    assert "liveDecisionPanelHtml" not in HTML


def test_racing_config_has_dirty_draft_ownership_and_awaited_save():
    assert "let racingConfigDirty=false" in HTML
    assert "RACING_CONFIG_IDS" in HTML
    assert "racingConfigMayHydrate()" in HTML
    assert "hydrateRacingConfigValues(vals" in HTML
    assert "async function saveRacingConfig()" in HTML
    assert "let r=await saveAll()" in HTML
    assert "racingConfigDirty=false" in HTML
    assert HTML.count('onclick="saveRacingConfig0941()"') == 1
    assert '>Save Changes</button>' in HTML
    # The global renderState hydrator must explicitly exclude dirty Racing controls.
    assert "(!RACING_CONFIG_IDS.has(id)||racingConfigMayHydrate())" in HTML


def test_shared_datetime_component_and_replay_popover_layout():
    assert "const SHARED_DATETIME_CONTROLS" in HTML
    assert "syncSharedDateTimeControl('performance')" in HTML
    assert "syncSharedDateTimeControl('results')" in HTML
    assert "syncSharedDateTimeControl('replay')" in HTML
    assert HTML.count("shared-datetime-range") >= 3
    assert ".custom-replay-range{" in HTML
    css = HTML.split(".custom-replay-range{", 1)[1].split("}", 1)[0]
    assert "position:absolute" in css
    assert "applyTimelineReplayCustomRange()" in HTML
    assert "applyPositionResultsCustomRange()" in HTML


def test_results_navigation_is_domain_owned_and_market_analysis_remains_global():
    analytics_nav = HTML.split('<div class="nav-subgroup" aria-label="Analytics navigation">', 1)[1].split('</div>', 1)[0]
    assert '>Results<' not in analytics_nav
    assert 'data-results-domain="sports"' in HTML
    assert 'data-results-domain="racing"' in HTML
    assert 'data-analytics-tab="market"' in analytics_nav
    assert "function openDomainResults" in HTML
    assert "domain:resultsDomain" in HTML


def test_settled_results_domain_is_enforced_server_side(tmp_path: Path):
    api = API(tmp_path / "v098.sqlite3")
    _add_settled(api, event="Sports Pre", sport="Football", section="sports", stream="pre_match", signature="sports-pre")
    _add_settled(api, event="Sports Live", sport="Tennis", section="sports", stream="in_play", signature="sports-live")
    _add_settled(api, event="Racing", sport="Greyhounds", section="racing", stream="racing", signature="racing")

    sports = api.settled_positions({"period": "all", "domain": "sports", "limit": 100})
    racing = api.settled_positions({"period": "all", "domain": "racing", "limit": 100})

    assert sports["domain"] == "sports"
    assert {row["monitor_stream"] for row in sports["rows"]} == {"pre_match", "in_play"}
    assert all(row["sport"] != "Greyhounds" for row in sports["rows"])
    assert racing["domain"] == "racing"
    assert len(racing["rows"]) == 1
    assert racing["rows"][0]["monitor_stream"] == "racing"
    assert racing["rows"][0]["sport"] == "Greyhounds"


def test_live_results_retains_domain_and_execution_stays_locked(tmp_path: Path):
    api = API(tmp_path / "live-results.sqlite3")
    r = api.live_results({"domain": "racing"})
    assert r["mode"] == "live"
    assert r["domain"] == "racing"
    assert r["rows"] == []
    assert r["live_execution_allowed"] is False
    state = api.get_state()
    assert state["settings"]["live_execution_available"] is False
