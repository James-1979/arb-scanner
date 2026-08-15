from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.models import ExchangeMarket, Quote
from arbscanner.scanner import Scanner
from arbscanner.secrets import SecretStore


def make_market(exchange, event_id, market_id, start, in_play=True):
    prices = [("a", "Alpha", 2.2), ("b", "Beta", 1.8)] if exchange.startswith("Betfair") else [("a2", "Alpha", 1.8), ("b2", "Beta", 2.2)]
    quotes = [
        Quote(exchange, event_id, market_id, "Alpha v Beta", "Match Winner", sid, sel, price, 100,
              datetime.now(timezone.utc).isoformat(), start, 0.0, "test", 1,
              "match winner", "two-way", "Tennis", in_play, "OPEN")
        for sid, sel, price in prices
    ]
    return ExchangeMarket(exchange, event_id, market_id, "Alpha v Beta", "Match Winner", start, quotes,
                          market_type="match winner", strategy="two-way", sport="Tennis", in_play=in_play)


class StaticFake:
    def __init__(self, name, market):
        self.name = name
        self.market = market

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


def scan_config(min_quality="Tiny"):
    return {
        "event_match_threshold": 0.5,
        "minimum_net_roi_pct": 0.1,
        "minimum_profit": 0,
        "minimum_liquidity": 2,
        "inplay_minimum_net_roi_pct": 0.1,
        "inplay_minimum_profit": 0,
        "inplay_minimum_liquidity": 2,
        "inplay_minimum_quality_band": min_quality,
        "research_two_way_enabled": True,
        "quality_reference_bankroll": 100,
        "execution_max_stake": 25,
        "inplay_execution_max_stake": 25,
        "max_bankroll_pct": 100,
        "max_event_exposure_pct": 100,
        "inplay_max_event_exposure_pct": 100,
        "price_quote_max_age_seconds": 10,
        "monitor_timing_checkpoints_ms": [1, 2, 3, 4],
        "monitor_timing_reference_checkpoint_ms": 2,
        "monitor_execution_checkpoint_ms": 3,
        "monitor_hedge_checkpoint_ms": 4,
        "execution_hedge_reserve_pct": 20,
        "inplay_execution_hedge_reserve_pct": 20,
        "execution_plan_ttl_ms": 1500,
        "execution_max_unhedged_exposure": 25,
        "inplay_execution_max_unhedged_exposure": 25,
        "execution_balance_tolerance": 0.10,
        "monitor_betfair_starting_balance": 250,
        "monitor_matchbook_starting_balance": 250,
        "inplay_monitor_enabled": True,
        "inplay_betfair_delay_ms": 0,
        "inplay_matchbook_delay_ms": 0,
        "inplay_adverse_odds_pct_per_second": 0,
        "inplay_liquidity_decay_pct_per_second": 0,
        "inplay_execution_max_slippage_pct": 1.5,
        "inplay_monitor_cooldown_seconds": 0,
        "one_recommendation_per_market": True,
    }


def test_quality_floor_blocks_lower_band_before_execution(tmp_path):
    start = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    bf = make_market("Betfair delayed", "be", "bm", start)
    mb = make_market("Matchbook", "me", "mm", start)
    db = DB(tmp_path / "quality-floor.sqlite3")
    db.set_setting("config", scan_config("Excellent"))
    scanner = Scanner(db, SecretStore())
    scanner._adapters = lambda mode="sim": [StaticFake("Betfair delayed", bf), StaticFake("Matchbook", mb)]

    scanner.discover_once()
    result = scanner.price_scan_once(force=True)

    row = dict(db.conn.execute("SELECT status,quality_band,reason FROM matched_markets ORDER BY id DESC LIMIT 1").fetchone())
    assert row["quality_band"] != "Excellent"
    assert row["status"] == "below_quality"
    assert "configured Excellent minimum" in row["reason"]
    assert result["pipeline"]["qualified"] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 0


def test_quality_settings_save_and_invalid_value_is_safely_normalized(tmp_path):
    api = API(tmp_path / "quality-config.sqlite3")
    state = api.save_settings({"config": {
        "pre_match_minimum_quality_band": "Usable",
        "inplay_minimum_quality_band": "Strong",
    }})
    assert state["version"] == "0.9.36"
    cfg = api.db.get_setting("config", {})
    assert cfg["pre_match_minimum_quality_band"] == "Usable"
    assert cfg["inplay_minimum_quality_band"] == "Strong"

    api.save_settings({"config": {"inplay_minimum_quality_band": "anything"}})
    assert api.db.get_setting("config", {})["inplay_minimum_quality_band"] == "Tiny"


def test_settings_landing_page_is_overview_not_form_dump():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "PoC 0.9.36" in html
    assert 'data-settings-pane-body="overview"' in html
    assert 'data-settings-pane-body="pre_match"' in html
    assert 'data-settings-pane-body="in_play"' in html
    assert 'data-settings-pane-body="scanner"' in html
    assert 'data-settings-pane-body="advanced"' in html
    overview = html.split('data-settings-pane-body="overview"', 1)[1].split('data-settings-pane-body="pre_match"', 1)[0]
    assert "Minimum displayed liquidity" not in overview
    assert "Betfair model delay" not in overview
    assert 'id="preMinQuality"' in html
    assert 'id="ipMinQuality"' in html
    assert "Risk & execution assumptions" in html
    assert "Risk & execution simulation" in html
    assert "Risk profile" in html
