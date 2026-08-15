from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.db import DB
from arbscanner.models import ExchangeMarket, Quote
from arbscanner.scanner import Scanner
from arbscanner.secrets import SecretStore


def _quote(exchange, eid, mid, sid, selection, odds, start, in_play=False):
    return Quote(
        exchange, eid, mid, "Alpha v Beta", "Match Winner", sid, selection,
        odds, 100, datetime.now(timezone.utc).isoformat(), start, 0.0, "test", 1,
        "match winner", "two-way", "Tennis", in_play, "OPEN"
    )


def test_fresh_inplay_confirmation_reclassifies_qualified_to_research(tmp_path):
    now = datetime.now(timezone.utc)
    start = (now + timedelta(minutes=5)).isoformat()
    bf_market = ExchangeMarket(
        "Betfair delayed", "be", "bm", "Alpha v Beta", "Match Winner", start,
        [_quote("Betfair delayed", "be", "bm", "a", "Alpha", 2.2, start),
         _quote("Betfair delayed", "be", "bm", "b", "Beta", 1.8, start)],
        market_type="match winner", strategy="two-way", sport="Tennis", in_play=False,
    )
    mb_market = ExchangeMarket(
        "Matchbook", "me", "mm", "Alpha vs Beta", "Match Winner", start,
        [_quote("Matchbook", "me", "mm", "a2", "Alpha", 1.8, start),
         _quote("Matchbook", "me", "mm", "b2", "Beta", 2.2, start)],
        market_type="match winner", strategy="two-way", sport="Tennis", in_play=False,
    )

    class Fake:
        def __init__(self, name, market):
            self.name, self.market = name, market
            self.state_calls = 0
        async def fetch_markets(self, horizon_hours=24, minimum_liquidity=0):
            return [self.market]
        async def fetch_market_state(self, event_id, market_id):
            self.state_calls += 1
            # The targeted price refresh sees the market as pre-match. The timed
            # execution check immediately afterwards is the authoritative in-play confirmation.
            in_play = self.state_calls > 1
            return {
                "ok": True, "exchange": self.name, "event_id": event_id, "market_id": market_id,
                "status": "OPEN", "in_play": in_play, "latency_ms": 1,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "quotes": {str(q.selection_id): {"odds": q.odds, "liquidity": q.liquidity} for q in self.market.quotes},
            }
        async def fetch_market_states(self, requests):
            return [await self.fetch_market_state(x["event_id"], x["market_id"]) for x in requests]

    db = DB(tmp_path / "audit.sqlite3")
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
        "inplay_monitor_enabled": False,
        "monitor_timing_checkpoints_ms": [1, 2, 3, 4],
        "monitor_timing_reference_checkpoint_ms": 2,
        "monitor_execution_checkpoint_ms": 3,
        "monitor_hedge_checkpoint_ms": 4,
    })
    scanner = Scanner(db, SecretStore())
    scanner._adapters = lambda mode="sim": [Fake("Betfair delayed", bf_market), Fake("Matchbook", mb_market)]

    scanner.discover_once()
    price = scanner.price_scan_once(force=True)
    assert price["pipeline"]["opportunities"] == 1
    assert price["pipeline"]["qualified"] == 0
    assert price["pipeline"]["executed"] == 0
    assert price["pipeline"]["in_play_research"] == 1
    assert price["pipeline"]["qualification_breakdown"].get("in_play_research") == 1

    opp = db.conn.execute("SELECT qualification_status,qualification_reason FROM opportunities").fetchone()
    assert opp["qualification_status"] == "in_play_research"
    assert "In-play research only" in opp["qualification_reason"]

    latest = db.latest_matched_markets()["rows"][0]
    assert latest["status"] == "in_play_research"

    period = db.scan_pipeline_between()
    assert period["qualified"] == 0
    assert period["in_play_research"] == 1
    assert period["qualification_breakdown"].get("in_play_research") == 1
    assert db.execution_failure_reasons_between(None, None) == {}


def test_dashboard_exposes_positive_opportunity_breakdown():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "PoC 0.9.36" in html
    assert "Positive opportunity outcomes:" in html
    assert "dashQualificationBreakdown" in html
    assert "monitorQualificationBreakdown" in html
