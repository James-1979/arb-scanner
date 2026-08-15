from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.db import DB
from arbscanner.models import ExchangeMarket, Quote
from arbscanner.scanner import Scanner
from arbscanner.secrets import SecretStore


def _quote(exchange, eid, mid, sid, selection, odds, start):
    return Quote(
        exchange, eid, mid, "Alpha v Beta", "Match Winner", sid, selection,
        odds, 100, datetime.now(timezone.utc).isoformat(), start, 0.0, "test", 1,
        "match winner", "two-way", "Tennis", True, "OPEN"
    )


def test_inplay_positive_candidate_is_monitor_research_only_and_never_executes(tmp_path):
    now = datetime.now(timezone.utc)
    start = (now - timedelta(minutes=5)).isoformat()
    bf_market = ExchangeMarket(
        "Betfair delayed", "be", "bm", "Alpha v Beta", "Match Winner", start,
        [_quote("Betfair delayed", "be", "bm", "a", "Alpha", 2.2, start),
         _quote("Betfair delayed", "be", "bm", "b", "Beta", 1.8, start)],
        market_type="match winner", strategy="two-way", sport="Tennis", in_play=True,
    )
    mb_market = ExchangeMarket(
        "Matchbook", "me", "mm", "Alpha vs Beta", "Match Winner", start,
        [_quote("Matchbook", "me", "mm", "a2", "Alpha", 1.8, start),
         _quote("Matchbook", "me", "mm", "b2", "Beta", 2.2, start)],
        market_type="match winner", strategy="two-way", sport="Tennis", in_play=True,
    )

    class Fake:
        def __init__(self, name, market):
            self.name, self.market = name, market
        async def fetch_markets(self, horizon_hours=24, minimum_liquidity=0):
            return [self.market]
        async def fetch_market_state(self, event_id, market_id):
            return {
                "ok": True, "exchange": self.name, "event_id": event_id, "market_id": market_id,
                "status": "OPEN", "in_play": True, "latency_ms": 1,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "quotes": {str(q.selection_id): {"odds": q.odds, "liquidity": q.liquidity} for q in self.market.quotes},
            }
        async def fetch_market_states(self, requests):
            return [await self.fetch_market_state(x["event_id"], x["market_id"]) for x in requests]

    db = DB(tmp_path / "inplay.sqlite3")
    db.set_setting("config", {
        "event_match_threshold": .5,
        "minimum_net_roi_pct": .1,
        "minimum_profit": 0,
        "minimum_liquidity": 2,
        "research_two_way_enabled": True,
        "quality_reference_bankroll": 100,
        "execution_max_stake": 25,
        "price_quote_max_age_seconds": 10,
        "price_refresh_near_seconds": 2,
        "price_refresh_today_seconds": 8,
        "price_refresh_later_seconds": 30,
        "execution_pre_match_only": True,
    })
    scanner = Scanner(db, SecretStore())
    scanner._adapters = lambda mode="sim": [Fake("Betfair delayed", bf_market), Fake("Matchbook", mb_market)]

    discovery = scanner.discover_once()
    assert discovery["matches"] == 1
    price = scanner.price_scan_once(force=True)
    assert price["pipeline"]["opportunities"] == 1
    assert price["pipeline"]["qualified"] == 0
    assert price["pipeline"]["executed"] == 0
    latest = db.latest_matched_markets()
    assert len(latest["rows"]) == 1
    row = latest["rows"][0]
    assert row["status"] == "in_play_research"
    assert row["in_play"] in (1, True)
    assert "no Monitor or LIVE orders" in row["reason"]
    assert db.conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 0


def test_frontend_uses_active_bets_and_inplay_research_warning():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "PoC 0.7.20" in html
    assert "Active bets" in html
    assert "Bets in play" not in html
    assert "Committed capital" in html
    assert "In-play research only." in html
    assert "IN-PLAY · OBSERVE ONLY" in html
    assert '<option value="inplay">In-play research</option>' in html
