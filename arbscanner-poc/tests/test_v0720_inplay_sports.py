from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.models import ExchangeMarket, Quote
from arbscanner.normalization import classify_market, match_markets
from arbscanner.scanner import Scanner
from arbscanner.secrets import SecretStore
from arbscanner.sports import SUPPORTED_SPORTS, normalize_sport


def _quote(exchange, eid, mid, sid, selection, odds, start, sport, in_play=True):
    return Quote(
        exchange, eid, mid, f"{selection} event", "Match Winner", sid, selection,
        odds, 100, datetime.now(timezone.utc).isoformat(), start, 0.0, "test", 1,
        "match winner", "two-way", sport, in_play, "OPEN"
    )


def test_inplay_research_is_timed_but_never_creates_execution(tmp_path):
    now = datetime.now(timezone.utc)
    start = (now - timedelta(minutes=5)).isoformat()
    bf_market = ExchangeMarket(
        "Betfair delayed", "be", "bm", "Alpha v Beta", "Match Winner", start,
        [
            Quote("Betfair delayed", "be", "bm", "Alpha v Beta", "Match Winner", "a", "Alpha", 2.2, 100, now.isoformat(), start, 0.0, "test", 1, "match winner", "two-way", "Tennis", True, "OPEN"),
            Quote("Betfair delayed", "be", "bm", "Alpha v Beta", "Match Winner", "b", "Beta", 1.8, 100, now.isoformat(), start, 0.0, "test", 1, "match winner", "two-way", "Tennis", True, "OPEN"),
        ],
        market_type="match winner", strategy="two-way", sport="Tennis", in_play=True,
    )
    mb_market = ExchangeMarket(
        "Matchbook", "me", "mm", "Alpha vs Beta", "Match Winner", start,
        [
            Quote("Matchbook", "me", "mm", "Alpha vs Beta", "Match Winner", "a2", "Alpha", 1.8, 100, now.isoformat(), start, 0.0, "test", 1, "match winner", "two-way", "Tennis", True, "OPEN"),
            Quote("Matchbook", "me", "mm", "Alpha vs Beta", "Match Winner", "b2", "Beta", 2.2, 100, now.isoformat(), start, 0.0, "test", 1, "match winner", "two-way", "Tennis", True, "OPEN"),
        ],
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
        "event_match_threshold": .5, "minimum_net_roi_pct": .1, "minimum_profit": 0,
        "minimum_liquidity": 2, "research_two_way_enabled": True, "quality_reference_bankroll": 100,
        "execution_max_stake": 25, "price_quote_max_age_seconds": 10,
        "price_refresh_inplay_seconds": 1, "price_refresh_near_seconds": 2,
        "price_refresh_today_seconds": 8, "price_refresh_later_seconds": 30,
        "execution_pre_match_only": True, "monitor_timing_checkpoints_ms": [1, 2, 3, 4],
        "monitor_timing_reference_checkpoint_ms": 2, "monitor_execution_checkpoint_ms": 3,
        "monitor_hedge_checkpoint_ms": 4,
        "inplay_monitor_enabled": False,
    })
    scanner = Scanner(db, SecretStore())
    scanner._adapters = lambda mode="sim": [Fake("Betfair delayed", bf_market), Fake("Matchbook", mb_market)]

    scanner.discover_once()
    result = scanner.price_scan_once(force=True)
    assert result["pipeline"]["opportunities"] == 1
    assert result["pipeline"]["qualified"] == 0
    assert result["pipeline"]["executed"] == 0

    opp = db.conn.execute("SELECT id,qualification_status FROM opportunities").fetchone()
    assert opp and opp["qualification_status"] == "in_play_research"
    run = db.conn.execute("SELECT research_only,reference_executable FROM monitor_timing_runs WHERE opportunity_id=?", (opp["id"],)).fetchone()
    assert run and run["research_only"] == 1
    assert run["reference_executable"] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM monitor_timing_observations").fetchone()[0] >= 5
    assert db.conn.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM monitor_positions").fetchone()[0] == 0

    metrics = db.monitor_timing_metrics(qualification_status="in_play_research")
    assert metrics["runs"] == 1
    assert metrics["reference_profit"] > 0
    assert metrics["survival"]["100"] == 0.0  # custom 1/2/3/4 ms test checkpoints


def test_new_individual_sports_and_market_safety():
    assert "Darts" in SUPPORTED_SPORTS
    assert "Snooker" in SUPPORTED_SPORTS
    assert normalize_sport("darts") == "Darts"
    assert normalize_sport("snooker") == "Snooker"
    assert classify_market("Match Winner", 2, "Darts") == ("match winner", "two-way")
    assert classify_market("Frame Winner", 2, "Snooker")[1] == "unknown"
    assert classify_market("Set Winner", 2, "Darts")[1] == "unknown"


def test_regulation_market_does_not_cross_match_full_game_market():
    start = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    def market(exchange, mid, name):
        quotes = [
            Quote(exchange, "e", mid, "Alpha v Beta", name, "a", "Alpha", 2.0, 50, datetime.now(timezone.utc).isoformat(), start, sport="Ice Hockey"),
            Quote(exchange, "e", mid, "Alpha v Beta", name, "b", "Beta", 2.0, 50, datetime.now(timezone.utc).isoformat(), start, sport="Ice Hockey"),
        ]
        return ExchangeMarket(exchange, "e", mid, "Alpha v Beta", name, start, quotes=quotes, sport="Ice Hockey")
    bf = market("Betfair delayed", "bf", "60 Minute Match Winner")
    mb = market("Matchbook", "mb", "Moneyline")
    assert match_markets([bf, mb], threshold=.5) == []


def test_pipeline_api_exposes_inplay_research_metrics_and_ui_labels(tmp_path):
    api = API(tmp_path / "api.sqlite3")
    r = api.pipeline_analytics({})
    assert "in_play_research" in r
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "PoC 0.9.36" in html
    assert "Markets checked" in html
    assert "In-play Monitor" in html
    assert 'id="monitorQualified"' in html
    assert 'id="monitorExecuted"' in html
    assert 'id="sportDarts"' in html
    assert 'id="sportSnooker"' in html
    assert 'id="priceInPlay"' in html


def test_upgrade_adds_research_only_to_existing_monitor_timing_runs(tmp_path):
    import sqlite3
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE monitor_timing_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'RUNNING',
            initial_deployed REAL NOT NULL DEFAULT 0,
            initial_profit REAL NOT NULL DEFAULT 0,
            initial_roi_pct REAL NOT NULL DEFAULT 0,
            planned_stakes_json TEXT,
            reference_checkpoint_ms INTEGER NOT NULL DEFAULT 250,
            survived_through_ms INTEGER NOT NULL DEFAULT 0,
            first_failure_reason TEXT,
            reference_profit REAL,
            reference_roi_pct REAL,
            reference_executable INTEGER
        );
    ''')
    conn.commit(); conn.close()
    db = DB(path)
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(monitor_timing_runs)")}
    assert "research_only" in cols
    db.conn.close()
