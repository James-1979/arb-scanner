from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

HTML = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text()


def test_v0842_release_contract_and_live_stays_locked():
    assert __version__ == "0.9.36"
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML
    assert 'id="marketHeatmapSport"' in HTML
    for metric in ("observations", "qualified", "executed", "pnl", "roi_pct", "deployed"):
        assert f'value="{metric}"' in HTML
    assert 'Sports in this period' in HTML
    assert 'Win / loss %' in HTML
    assert 'Event result' in HTML
    assert 'LIVE remains locked' in HTML or 'LIVE is locked' in HTML


def test_exchange_native_discovery_keeps_betfair_only_greyhounds_and_dedupes_phase(tmp_path):
    api = API(tmp_path / "discovery.sqlite3")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = [
        {"exchange":"Betfair delayed","market_id":"bf-1","event_id":"e1","event_name":"Romford R1","market_name":"Win","sport":"Greyhounds","section":"racing","in_play":False,"canonical_market_key":"romford-r1|win"},
        {"exchange":"Betfair delayed","market_id":"bf-1","event_id":"e1","event_name":"Romford R1","market_name":"Win","sport":"Greyhounds","section":"racing","in_play":True,"canonical_market_key":"romford-r1|win"},
        {"exchange":"Betfair delayed","market_id":"bf-2","event_id":"e2","event_name":"Romford R2","market_name":"Win","sport":"Greyhounds","section":"racing","in_play":False},
        {"exchange":"Betfair delayed","market_id":"bf-3","event_id":"e3","event_name":"Romford R3","market_name":"Win","sport":"Greyhounds","section":"racing","in_play":False},
        {"exchange":"Matchbook","market_id":"mb-1","event_id":"m1","event_name":"Romford R1","market_name":"Win","sport":"Greyhounds","section":"racing","in_play":False,"canonical_market_key":"romford-r1|win"},
        {"exchange":"Matchbook","market_id":"mb-4","event_id":"m4","event_name":"Romford R4","market_name":"Win","sport":"Greyhounds","section":"racing","in_play":False},
    ]
    api.db.record_exchange_market_discoveries(rows, now.isoformat())
    # Re-observing the same native market cannot increase unique coverage.
    api.db.record_exchange_market_discoveries([rows[0]], (now + timedelta(minutes=5)).isoformat())
    result = api.market_analysis({
        "from_utc": (now - timedelta(hours=1)).isoformat(),
        "to_utc": (now + timedelta(hours=1)).isoformat(),
        "scope": "racing", "phase": "all", "sport": "Greyhounds",
    })
    comp = result["exchange_comparator"]
    assert comp["betfair_markets"] == 3
    assert comp["matchbook_markets"] == 2
    assert comp["overlap_markets"] == 1
    assert comp["betfair_markets"] > comp["matchbook_markets"]


def test_exchange_discovery_custom_period_respects_partial_hour_boundaries(tmp_path):
    api = API(tmp_path / "discovery-boundary.sqlite3")
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    api.db.record_exchange_market_discoveries([
        {"exchange":"Betfair delayed","market_id":"early","event_name":"Early race","market_name":"Win","sport":"Greyhounds","section":"racing"},
    ], (base + timedelta(minutes=5)).isoformat())
    api.db.record_exchange_market_discoveries([
        {"exchange":"Betfair delayed","market_id":"inside","event_name":"Inside race","market_name":"Win","sport":"Greyhounds","section":"racing"},
    ], (base + timedelta(minutes=45)).isoformat())
    rows = api.db.exchange_market_discovery_between(
        (base + timedelta(minutes=30)).isoformat(),
        (base + timedelta(minutes=55)).isoformat(),
    )
    assert {r["market_id"] for r in rows} == {"inside"}


def test_historical_racing_catalogue_backfill_preserves_betfair_only_markets(tmp_path):
    api = API(tmp_path / "historical-catalogue.sqlite3")
    scan_id = api.db.start_scan(scan_kind="discovery")
    statuses = [
        {"exchange":"Betfair delayed","racing_discovery":{"rows":[
            {"market_id":"bf-cat-1","event_id":"e1","event_name":"Romford R1","market_name":"Win"},
            {"market_id":"bf-cat-2","event_id":"e2","event_name":"Romford R2","market_name":"Win"},
        ]}},
        {"exchange":"Matchbook","racing_discovery":{"rows":[
            {"market_id":"mb-cat-1","event_id":"m1","event_name":"Romford R1","market_name":"Win"},
        ]}},
    ]
    api.db.finish_scan(scan_id, statuses=statuses)
    now = datetime.now(timezone.utc)
    rows = api.db.exchange_market_discovery_between((now - timedelta(hours=1)).isoformat(), (now + timedelta(hours=1)).isoformat())
    bf = {r["market_id"] for r in rows if r["exchange_key"] == "betfair"}
    mb = {r["market_id"] for r in rows if r["exchange_key"] == "matchbook"}
    assert bf == {"bf-cat-1", "bf-cat-2"}
    assert mb == {"mb-cat-1"}


def test_heatmap_reads_compact_rollups_and_returns_all_sport_metrics(tmp_path, monkeypatch):
    api = API(tmp_path / "heatmap.sqlite3")
    db = api.db
    hour = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    hour_iso = hour.isoformat()
    built = datetime.now(timezone.utc).isoformat()
    with db.lock:
        db.conn.execute("INSERT INTO market_hourly_rollups(hour_utc,section,sport,market_name,in_play,observations,unique_markets,net_positive) VALUES(?,?,?,?,?,?,?,?)",
                        (hour_iso,"sports","Football","Match Odds",0,120,8,4))
        db.conn.execute("INSERT INTO market_hourly_rollup_state(hour_utc,built_at) VALUES(?,?)",(hour_iso,built))
        db.conn.execute("INSERT INTO market_financial_hourly_rollups(hour_utc,section,sport,market_name,in_play,qualified,executed,deployed,settled,settled_deployed,pnl) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (hour_iso,"sports","Football","Match Odds",0,7,3,180.0,2,100.0,5.0))
        db.conn.execute("INSERT INTO market_financial_hourly_state(hour_utc,built_at) VALUES(?,?)",(hour_iso,built))
        db.conn.commit()
    # A fully materialised past hour must not rebuild from canonical history.
    monkeypatch.setattr(db, "_financial_hour_rows_between", lambda *_: (_ for _ in ()).throw(AssertionError("history regrouped")))
    result = api.market_heatmap({
        "from_utc": hour.isoformat(), "to_utc": (hour + timedelta(hours=1)).isoformat(),
        "scope":"sports", "phase":"pre_match", "timezone_name":"UTC", "timezone_offset_minutes":0,
    })
    assert result["ok"] is True
    assert result["source"] == "compact_hourly_rollups"
    assert "Football" in result["sports"]
    cell = next(x for x in result["by_sport"]["Football"] if x["date"] == hour.date().isoformat() and x["hour"] == hour.hour)
    assert cell["observations"] == 120
    assert cell["qualified"] == 7
    assert cell["executed"] == 3
    assert cell["deployed"] == 180.0
    assert cell["pnl"] == 5.0
    assert cell["roi_pct"] == 5.0
    assert cell["observed"] is True
    assert result["metrics"] == ["observations","qualified","executed","pnl","roi_pct","deployed","available_depth","avg_executable_stake","liquidity_capable","liquidity_rejection_rate_pct"]


def test_settled_results_expose_canonical_event_outcome_not_inferred_from_pnl(tmp_path):
    api = API(tmp_path / "results.sqlite3")
    db = api.db
    now = datetime.now(timezone.utc).isoformat()
    oid = db.add_opportunity("e1","Alpha v Beta",None,"Match Winner",2.0,2.0,[],[],1.0,"sig-0842",sport="Football",section="sports",in_play=False)
    with db.lock:
        db.conn.execute("""INSERT INTO monitor_positions(opportunity_id,event_key,market_name,opened_at,settled_at,status,deployed,expected_profit,
                         stakes_by_exchange_json,outcome_exchange_pnls_json,simulation_json,stream,currency,outcome,realized_pnl,realized_by_exchange_json)
                         VALUES(?,?,?,?,?,'SETTLED',?,?,?,?,?,'pre_match','GBP',?,?,?)""",
                        (oid,"e1","Match Winner",now,now,100.0,2.0,'{}','{}','{}',"Beta",4.25,'{}'))
        db.conn.execute("INSERT INTO settlements(opportunity_id,settled_at,outcome,simulated_pnl,notes) VALUES(?,?,?,?,?)",(oid,now,"Beta",4.25,"test"))
        db.conn.commit()
    r = api.settled_positions({"period":"all","limit":100})
    assert r["ok"] is True and len(r["rows"]) == 1
    row = r["rows"][0]
    assert row["event_result"] == "Beta"
    assert row["result_available"] is True
    assert row["final_pnl"] == 4.25
    # The actual winner remains Beta even though this position itself is profitable.
    assert row["event_result"] != "WIN"


def test_results_percentage_contract_uses_visible_decided_cohort_and_excludes_break_even():
    assert "decided=wins+losses" in HTML
    assert "winPct=decided?100*wins/decided:0" in HTML
    assert "lossPct=decided?100*losses/decided:0" in HTML
    assert "decided · ${be.toLocaleString()} break-even" in HTML
    # Independent dashboard 24h data must no longer exist in the Results helper.
    block = HTML.split("async function loadResultsIntegrityTiles(){",1)[1].split("async function loadPositionResults",1)[0]
    assert "dashboard_results_24h" not in block
    assert HTML.count("function loadResultsIntegrityTiles") == 1


def test_replay_payload_has_compact_period_activity_index(tmp_path):
    api = API(tmp_path / "replay.sqlite3")
    r = api.activity_analytics({"include_results":False,"include_executions":True,"include_metrics":False,"include_all_time":False,"timeline_range":True,"limit":10})
    assert r["ok"] is True
    assert r["period_activity"] == {"sports":[],"engines":[],"markets":[]}


def test_replay_tiles_consume_compact_period_activity_index():
    block = HTML.split("function renderReplayActivityTiles0842(){",1)[1].split("function replayActivitySelectSport",1)[0]
    assert "replayActivityIndex0842()" in block
    assert "timelineReplayPositions||[]" not in block
    assert "wins||0" in block and "losses||0" in block
