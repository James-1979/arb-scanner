from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.models import ExchangeMarket, Quote
from arbscanner.scanner import Scanner
from arbscanner.secrets import SecretStore


def make_market(exchange, event_id, market_id, start, in_play):
    if exchange.startswith("Betfair"):
        odds = [("a", "Alpha", 2.2), ("b", "Beta", 1.8)]
    else:
        odds = [("a2", "Alpha", 1.8), ("b2", "Beta", 2.2)]
    quotes = [
        Quote(exchange, event_id, market_id, "Alpha v Beta", "Match Winner", sid, sel, price, 100,
              datetime.now(timezone.utc).isoformat(), start, 0.0, "test", 1,
              "match winner", "two-way", "Tennis", in_play, "OPEN")
        for sid, sel, price in odds
    ]
    return ExchangeMarket(exchange, event_id, market_id, "Alpha v Beta", "Match Winner", start, quotes,
                          market_type="match winner", strategy="two-way", sport="Tennis", in_play=in_play)


class StaticFake:
    def __init__(self, name, market, state_in_play=True):
        self.name = name
        self.market = market
        self.state_in_play = state_in_play

    async def fetch_markets(self, horizon_hours=24, minimum_liquidity=0):
        return [self.market]

    async def fetch_market_state(self, event_id, market_id):
        return {
            "ok": True,
            "exchange": self.name,
            "event_id": event_id,
            "market_id": market_id,
            "status": "OPEN",
            "in_play": self.state_in_play,
            "latency_ms": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "quotes": {str(q.selection_id): {"odds": q.odds, "liquidity": q.liquidity} for q in self.market.quotes},
        }

    async def fetch_market_states(self, requests):
        return [await self.fetch_market_state(x["event_id"], x["market_id"]) for x in requests]


def base_config():
    return {
        "event_match_threshold": 0.5,
        "minimum_net_roi_pct": 0.1,
        "minimum_profit": 0,
        "minimum_liquidity": 2,
        "research_two_way_enabled": True,
        "research_1x2_enabled": True,
        "quality_reference_bankroll": 100,
        "execution_max_stake": 25,
        "max_bankroll_pct": 100,
        "max_event_exposure_pct": 100,
        "price_quote_max_age_seconds": 10,
        "price_refresh_inplay_seconds": 1,
        "price_refresh_near_seconds": 2,
        "price_refresh_today_seconds": 8,
        "price_refresh_later_seconds": 30,
        "execution_pre_match_only": True,
        "monitor_timing_checkpoints_ms": [1, 2, 3, 4],
        "monitor_timing_reference_checkpoint_ms": 2,
        "monitor_execution_checkpoint_ms": 3,
        "monitor_hedge_checkpoint_ms": 4,
        "execution_hedge_reserve_pct": 20,
        "execution_plan_ttl_ms": 1500,
        "execution_max_slippage_pct": 0.5,
        "execution_max_unhedged_exposure": 25,
        "execution_balance_tolerance": 0.10,
        "monitor_betfair_starting_balance": 250,
        "monitor_matchbook_starting_balance": 250,
        "inplay_monitor_enabled": True,
        "inplay_betfair_delay_ms": 0,
        "inplay_matchbook_delay_ms": 0,
        "inplay_adverse_odds_pct_per_second": 0,
        "inplay_liquidity_decay_pct_per_second": 0,
        "inplay_execution_max_slippage_pct": 1.5,
        "one_recommendation_per_market": True,
    }


def test_inplay_opportunity_can_open_separate_monitor_position(tmp_path):
    start = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    bf = make_market("Betfair delayed", "be", "bm", start, True)
    mb = make_market("Matchbook", "me", "mm", start, True)
    db = DB(tmp_path / "inplay.sqlite3")
    db.set_setting("config", base_config())
    scanner = Scanner(db, SecretStore())
    scanner._adapters = lambda mode="sim": [StaticFake("Betfair delayed", bf, True), StaticFake("Matchbook", mb, True)]

    scanner.discover_once()
    result = scanner.price_scan_once(force=True)

    assert result["pipeline"]["opportunities"] == 1
    assert result["pipeline"]["qualified"] == 1
    assert result["pipeline"]["in_play_qualified"] == 1

    opp = dict(db.conn.execute("SELECT id,qualification_status FROM opportunities").fetchone())
    assert opp["qualification_status"] == "in_play_qualified"

    runs = [dict(r) for r in db.conn.execute("SELECT * FROM execution_runs WHERE opportunity_id=?", (opp["id"],)).fetchall()]
    assert len(runs) == 1
    details = __import__("json").loads(runs[0]["details_json"] or "{}")
    assert details["monitor_stream"] == "in_play"
    assert details["live_order_placement"] is False

    pos = db.conn.execute("SELECT stream,status FROM monitor_positions WHERE opportunity_id=?", (opp["id"],)).fetchone()
    assert pos is not None
    assert pos["stream"] == "in_play"
    assert pos["status"] == "OPEN"

    pm = db.monitor_wallet_snapshot(20, "pre_match")
    ip = db.monitor_wallet_snapshot(20, "in_play")
    assert pm["betfair"]["reserved"] == 0
    assert pm["matchbook"]["reserved"] == 0
    assert ip["betfair"]["reserved"] > 0 or ip["matchbook"]["reserved"] > 0


def test_pre_match_to_inplay_handoff_does_not_create_duplicate_missed_execution(tmp_path):
    start = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    bf = make_market("Betfair delayed", "be", "bm", start, False)
    mb = make_market("Matchbook", "me", "mm", start, False)

    class TransitionFake(StaticFake):
        def __init__(self, name, market):
            super().__init__(name, market, False)
            self.state_calls = 0

        async def fetch_market_state(self, event_id, market_id):
            self.state_calls += 1
            self.state_in_play = self.state_calls > 1
            return await super().fetch_market_state(event_id, market_id)

    db = DB(tmp_path / "handoff.sqlite3")
    db.set_setting("config", base_config())
    scanner = Scanner(db, SecretStore())
    scanner._adapters = lambda mode="sim": [TransitionFake("Betfair delayed", bf), TransitionFake("Matchbook", mb)]

    scanner.discover_once()
    result = scanner.price_scan_once(force=True)
    assert result["pipeline"]["qualified"] == 1

    opp = dict(db.conn.execute("SELECT id,qualification_status FROM opportunities").fetchone())
    assert opp["qualification_status"] == "in_play_qualified"
    runs = [dict(r) for r in db.conn.execute("SELECT * FROM execution_runs WHERE opportunity_id=? ORDER BY id", (opp["id"],)).fetchall()]
    assert len(runs) == 1
    details = __import__("json").loads(runs[0]["details_json"] or "{}")
    assert details.get("monitor_stream") == "in_play"
    assert "MONITOR_MISSED" not in {r["state"] for r in runs}


def test_upgrade_creates_independent_stream_wallets_and_preserves_pre_match(tmp_path):
    db = DB(tmp_path / "upgrade.sqlite3")
    db.ensure_monitor_wallets({"betfair": 300, "matchbook": 200}, "pre_match")
    db.ensure_monitor_streams({"betfair": 300, "matchbook": 200})
    streams = db.monitor_wallets_by_stream(20)
    assert streams["pre_match"]["betfair"]["opening_balance"] == 300
    assert streams["pre_match"]["matchbook"]["opening_balance"] == 200
    assert streams["in_play"]["betfair"]["opening_balance"] == 300
    assert streams["in_play"]["matchbook"]["opening_balance"] == 200


def test_api_and_ui_expose_stream_comparison_and_keep_live_locked(tmp_path):
    api = API(tmp_path / "api.sqlite3")
    overview = api.dashboard_overview({})
    assert set(overview["stream_summary"]) == {"pre_match", "in_play", "racing"}
    assert overview["delayed_feed_warning"]
    pipe = api.pipeline_analytics({})
    assert "in_play_monitor" in pipe

    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "PoC 0.9.36" in html
    assert 'id="monitorPhase"' in html
    assert 'id="executionsPhase"' in html
    assert "Monitor stream comparison" not in html
    assert "SIMULATED · LIVE LOCKED" in html
    assert "No LIVE in-play orders can be placed" in html
    assert "Markets checked" in html


def test_real_pre0721_wallet_schema_migrates_into_pre_match_stream(tmp_path):
    import sqlite3
    path = tmp_path / "legacy0720.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE monitor_wallets (
            exchange TEXT PRIMARY KEY,
            opening_balance REAL NOT NULL DEFAULT 0,
            available_balance REAL NOT NULL DEFAULT 0,
            reserved_balance REAL NOT NULL DEFAULT 0,
            realized_pnl REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
    ''')
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO monitor_wallets VALUES(?,?,?,?,?,?)", ("betfair", 300, 275, 25, 0, now))
    conn.execute("INSERT INTO monitor_wallets VALUES(?,?,?,?,?,?)", ("matchbook", 200, 180, 20, 0, now))
    conn.commit(); conn.close()

    db = DB(path)
    pm = db.monitor_wallet_snapshot(20, "pre_match")
    assert pm["betfair"]["opening_balance"] == 300
    assert pm["betfair"]["available"] == 275
    assert pm["betfair"]["reserved"] == 25
    assert pm["matchbook"]["opening_balance"] == 200
    assert pm["matchbook"]["available"] == 180
    assert pm["matchbook"]["reserved"] == 20
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(monitor_positions)")}
    monitor_timing_cols = {r[1] for r in db.conn.execute("PRAGMA table_info(monitor_timing_runs)")}
    assert "stream" in cols
    assert "stream" in monitor_timing_cols


def test_replay_exposes_pre_match_inplay_and_combined_comparison(tmp_path):
    api = API(tmp_path / "replay.sqlite3")
    r = api.analytics_replay({
        "starting_capital": 500,
        "from_utc": "2026-08-10T00:00:00+00:00",
        "to_utc": "2026-08-11T00:00:00+00:00",
    })
    assert r["ok"] is True
    assert set(r["stream_comparison"]) == {"pre_match", "in_play", "combined"}


def _seed_inplay_opportunity(db, cache_row, *, seconds_ago=0):
    oid = db.add_opportunity(
        cache_row["event_key"], cache_row["event_name"], cache_row["event_start"], cache_row["market_name"],
        5.0, 5.0, [], cache_row.get("source_markets") or [], cache_row.get("match_score") or 0.9,
        f"seed-{seconds_ago}", strategy=cache_row.get("strategy") or "two-way",
        sport=cache_row.get("sport") or "Tennis", in_play=True, event_status="OPEN",
    )
    when = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
    db.conn.execute(
        "UPDATE opportunities SET detected_at=?,qualification_status='in_play_qualified' WHERE id=?",
        (when, oid),
    )
    db.conn.commit()
    return oid


def test_historical_inplay_opportunity_does_not_block_fresh_attempt(tmp_path):
    cfg = base_config(); cfg["inplay_monitor_cooldown_seconds"] = 8
    start = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    bf = make_market("Betfair delayed", "be", "bm", start, True)
    mb = make_market("Matchbook", "me", "mm", start, True)
    db = DB(tmp_path / "historical-retry.sqlite3")
    db.set_setting("config", cfg)
    scanner = Scanner(db, SecretStore())
    scanner._adapters = lambda mode="sim": [StaticFake("Betfair delayed", bf, True), StaticFake("Matchbook", mb, True)]

    scanner.discover_once()
    cache = db.active_market_cache(limit=10)[0]
    old_id = _seed_inplay_opportunity(db, cache, seconds_ago=60)

    result = scanner.price_scan_once(force=True)
    assert result["pipeline"]["opportunities"] == 1
    assert result["pipeline"]["qualified"] == 1
    assert result["pipeline"]["executed"] == 1
    rows = [dict(r) for r in db.conn.execute("SELECT id,qualification_status FROM opportunities ORDER BY id").fetchall()]
    assert len(rows) == 2
    assert rows[-1]["id"] != old_id
    assert rows[-1]["qualification_status"] == "in_play_qualified"


def test_recent_inplay_attempt_is_short_cooldown_not_lifetime_dedupe(tmp_path):
    cfg = base_config(); cfg["inplay_monitor_cooldown_seconds"] = 8
    start = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    bf = make_market("Betfair delayed", "be", "bm", start, True)
    mb = make_market("Matchbook", "me", "mm", start, True)
    db = DB(tmp_path / "cooldown.sqlite3")
    db.set_setting("config", cfg)
    scanner = Scanner(db, SecretStore())
    scanner._adapters = lambda mode="sim": [StaticFake("Betfair delayed", bf, True), StaticFake("Matchbook", mb, True)]

    scanner.discover_once()
    cache = db.active_market_cache(limit=10)[0]
    _seed_inplay_opportunity(db, cache, seconds_ago=1)

    result = scanner.price_scan_once(force=True)
    assert result["pipeline"]["opportunities"] == 1
    assert result["pipeline"]["qualified"] == 0
    assert result["pipeline"]["executed"] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 1
    latest = dict(db.conn.execute("SELECT status,reason FROM matched_markets ORDER BY id DESC LIMIT 1").fetchone())
    assert latest["status"] == "in_play_cooldown"
    assert "8s" in latest["reason"]


def test_open_inplay_position_blocks_duplicate_even_after_cooldown_setting_zero(tmp_path):
    cfg = base_config(); cfg["inplay_monitor_cooldown_seconds"] = 0
    start = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    bf = make_market("Betfair delayed", "be", "bm", start, True)
    mb = make_market("Matchbook", "me", "mm", start, True)
    db = DB(tmp_path / "open-position.sqlite3")
    db.set_setting("config", cfg)
    scanner = Scanner(db, SecretStore())
    scanner._adapters = lambda mode="sim": [StaticFake("Betfair delayed", bf, True), StaticFake("Matchbook", mb, True)]

    scanner.discover_once()
    first = scanner.price_scan_once(force=True)
    assert first["pipeline"]["executed"] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM monitor_positions WHERE status='OPEN' AND stream='in_play'").fetchone()[0] == 1

    second = scanner.price_scan_once(force=True)
    assert second["pipeline"]["opportunities"] == 1
    assert second["pipeline"]["qualified"] == 0
    assert second["pipeline"]["executed"] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 1
    latest = dict(db.conn.execute("SELECT status,reason FROM matched_markets ORDER BY id DESC LIMIT 1").fetchone())
    assert latest["status"] == "in_play_position_open"
