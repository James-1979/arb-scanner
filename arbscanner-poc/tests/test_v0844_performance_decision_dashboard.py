import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _settled(api, *, name, sport, market, stream, deployed, expected, pnl, bf_pnl, mb_pnl, hours_ago):
    now = datetime.now(timezone.utc)
    opened = now - timedelta(hours=hours_ago)
    settled = opened + timedelta(minutes=40)
    legs = [
        {"exchange": "Betfair", "venue_id": "betfair", "provider_id": "betfair", "side": "BACK", "selection": "A", "stake": deployed * 0.45, "odds": 2.1},
        {"exchange": "Matchbook", "venue_id": "matchbook", "provider_id": "matchbook", "side": "LAY", "selection": "A", "stake": deployed * 0.55, "odds": 2.0},
    ]
    oid = api.db.add_opportunity(
        event_key=f"{name}-{stream}", event_name=name, event_start=opened.isoformat(), market_name=market,
        edge_pct=expected / deployed * 100.0, expected_roi_pct=expected / deployed * 100.0,
        legs=legs, source_markets=[], match_score=1.0, signature=f"sig-{name}-{stream}", sport=sport,
    )
    with api.db.lock:
        api.db.conn.execute("UPDATE opportunities SET qualification_status=? WHERE id=?", ("qualified", oid))
        api.db.conn.execute(
            """INSERT INTO monitor_positions(
                opportunity_id,event_key,market_name,opened_at,settled_at,status,deployed,expected_profit,
                stakes_by_exchange_json,outcome_exchange_pnls_json,simulation_json,stream,outcome,realized_pnl,realized_by_exchange_json
            ) VALUES(?,?,?,?,?,'SETTLED',?,?,?,?,?,?,?,?,?)""",
            (
                oid, f"{name}-{stream}", market, opened.isoformat(), settled.isoformat(), float(deployed), float(expected),
                json.dumps({"betfair": deployed * 0.45, "matchbook": deployed * 0.55}), "{}", "{}", stream, "A", float(pnl),
                json.dumps({"betfair": bf_pnl, "matchbook": mb_pnl}),
            ),
        )
        api.db.conn.commit()
    return oid


def test_v0844_performance_metrics_reconcile_across_domain_venue_and_pair(tmp_path):
    api = API(tmp_path / "decision-performance.sqlite3")
    api.dashboard_overview({})
    _settled(api, name="Sports Event", sport="Football", market="Match Odds", stream="pre_match", deployed=100, expected=10, pnl=8, bf_pnl=3, mb_pnl=5, hours_ago=8)
    _settled(api, name="Racing Event", sport="Greyhounds", market="Win", stream="racing", deployed=50, expected=5, pnl=2, bf_pnl=1, mb_pnl=1, hours_ago=5)

    r = api.performance_analytics({"period": "7d", "scope": "all", "stream": "all", "basis": "actual", "timezone_offset_minutes": 0})
    assert r["ok"] is True
    assert r["summary"]["net_pnl"] == 10.0
    assert r["summary"]["deployed_turnover"] == 150.0
    assert r["summary"]["captured_edge_pct"] == round(10 / 15 * 100, 4)

    domains = {x["key"]: x for x in r["performance"]["domains"]}
    assert domains["sports"]["pnl"] + domains["racing"]["pnl"] == r["summary"]["net_pnl"]
    assert domains["sports"]["capital_deployed"] + domains["racing"]["capital_deployed"] == r["summary"]["deployed_turnover"]

    pair = r["performance"]["venue_pairs"][0]
    assert pair["pair"] == "Betfair ↔ Matchbook"
    assert pair["positions"] == 2
    # Pair attribution is position-level: capital/P&L are not doubled for two legs.
    assert pair["capital_deployed"] == 150.0
    assert pair["pnl"] == 10.0

    venues = {x["venue_id"]: x for x in r["performance"]["venues"]}
    assert venues["betfair"]["positions"] == 2
    assert venues["matchbook"]["positions"] == 2
    assert venues["betfair"]["pnl_contribution"] + venues["matchbook"]["pnl_contribution"] == 10.0


def test_v0844_performance_filters_and_90_day_period_are_supported(tmp_path):
    api = API(tmp_path / "decision-performance-filter.sqlite3")
    api.dashboard_overview({})
    _settled(api, name="Football Event", sport="Football", market="Match Odds", stream="pre_match", deployed=100, expected=8, pnl=6, bf_pnl=3, mb_pnl=3, hours_ago=6)
    _settled(api, name="Tennis Event", sport="Tennis", market="Match Winner", stream="in_play", deployed=90, expected=9, pnl=4, bf_pnl=2, mb_pnl=2, hours_ago=4)

    r = api.performance_analytics({"period": "90d", "scope": "sports", "stream": "all", "sport": "Football", "venue_pair": "betfair|matchbook", "basis": "actual", "timezone_offset_minutes": 0})
    assert r["filters"]["period"] == "90d"
    assert r["summary"]["settled_bets"] == 1
    assert r["summary"]["net_pnl"] == 6.0
    assert all(x["sport"] == "Football" for x in r["performance"]["markets"])


def test_v0844_spacing_contract_and_release_identity():
    assert __version__ == "0.9.36"
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML
    assert 'market-discovery-grid{flex:0 0 158px;min-height:158px' in HTML
    assert 'grid-template-rows:auto auto auto auto auto auto minmax(132px,1fr)' in HTML
    assert 'height:228px!important;min-height:228px!important' in HTML
