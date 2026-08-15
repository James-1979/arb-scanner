from datetime import datetime, timedelta, timezone
from dataclasses import asdict
from pathlib import Path

from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.models import Leg
from arbscanner.monitor_timing import evaluate_observation


def test_market_cache_persists_and_respects_due_interval(tmp_path):
    path = tmp_path / "arb.sqlite3"
    db = DB(path)
    now = datetime.now(timezone.utc)
    row = {
        "cache_key": "abc", "event_key": "a-v-b", "event_name": "A v B",
        "event_start": (now + timedelta(hours=1)).isoformat(), "market_name": "Match Winner",
        "market_type": "match winner", "strategy": "two-way", "sport": "Tennis",
        "match_score": 0.91, "source_markets": [{"exchange": "Betfair delayed", "event_id": "1", "market_id": "m1"}],
        "refresh_interval_seconds": 3,
    }
    db.upsert_market_cache([row])
    assert db.market_cache_stats()["active"] == 1
    assert len(db.active_market_cache(due_at=now.isoformat())) == 1
    db.mark_market_cache_refreshed(["abc"], refreshed_at=now.isoformat())
    assert db.active_market_cache(due_at=(now + timedelta(seconds=1)).isoformat()) == []
    db.conn.close()
    db2 = DB(path)
    assert db2.market_cache_stats()["active"] == 1
    assert db2.active_market_cache()[0]["cache_key"] == "abc"


def test_discovery_and_price_scan_metrics_are_separate_and_canonical(tmp_path):
    api = API(tmp_path / "arb.sqlite3")
    # One slow discovery scan.
    sid = api.db.start_scan(scan_kind="discovery")
    api.db.finish_scan(sid, markets_seen=100, matches_seen=40, opportunities_found=0, duration_ms=4000, cache_entries=40)
    # Two fast price scans.
    for processed, opps in ((50, 2), (60, 3)):
        sid = api.db.start_scan(scan_kind="price")
        api.db.finish_scan(sid, markets_seen=80, matches_seen=35, opportunities_found=opps,
                           processed_candidates=processed, positive_opportunities=opps,
                           qualified_count=99, executed_count=99, duration_ms=600)

    legs = [Leg("Betfair delayed", "A", 2.2, 50, market_id="bf", selection_id="a"),
            Leg("Matchbook", "B", 2.2, 50, market_id="mb", selection_id="b")]
    oid = api.db.add_opportunity("evt", "A v B", None, "Match Winner", 5, 5,
                                 [asdict(x) for x in legs], [], .9, "sig", sport="Tennis")
    # Canonical Executed is based on a Monitor position, not the scan counter.
    api.db.ensure_monitor_wallets({"betfair": 100, "matchbook": 100})
    api.db.open_monitor_position(opportunity_id=oid, execution_run_id=None, event_key="evt", market_name="Match Winner",
                                 deployed=20, expected_profit=1, stakes_by_exchange={"betfair": 10, "matchbook": 10},
                                 outcome_exchange_pnls={"A": {"betfair": 1, "matchbook": 0}, "B": {"betfair": 0, "matchbook": 1}},
                                 simulation={"stakes": []}, hedge_reserve_pct=0)

    r = api.pipeline_analytics({})
    assert r["pipeline"]["scans"] == 2
    assert r["pipeline"]["processed"] == 110
    assert r["pipeline"]["opportunities"] == 5
    assert r["pipeline"]["qualified"] == 1
    assert r["pipeline"]["executed"] == 1
    assert r["discovery"]["scans"] == 1
    assert r["discovery"]["fetched"] == 100
    assert r["discovery"]["matched"] == 40


def test_latest_matched_markets_ignores_newer_discovery_scan(tmp_path):
    db = DB(tmp_path / "arb.sqlite3")
    price = db.start_scan(scan_kind="price")
    db.add_matched_market(price, "e", "A v B", None, "Match Winner", .9, 1, 1, 0, 1, 10, 1, None,
                          "recommended", "ok", [], [], strategy="two-way", sport="Tennis")
    db.finish_scan(price, 2, 1, 1, processed_candidates=1, positive_opportunities=1, qualified_count=1)
    discovery = db.start_scan(scan_kind="discovery")
    db.finish_scan(discovery, 100, 40, 0)
    latest = db.latest_matched_markets()
    assert latest["scan"]["id"] == price
    assert len(latest["rows"]) == 1


def test_inplay_failure_records_specific_venue():
    legs = [Leg("Betfair delayed", "A", 2.1, 50, market_id="bf", selection_id="a"),
            Leg("Matchbook", "B", 2.1, 50, market_id="mb", selection_id="b")]
    original = {"stakes": [
        {"exchange": "Betfair delayed", "market_id": "bf", "selection_id": "a", "selection": "A", "stake": 10},
        {"exchange": "Matchbook", "market_id": "mb", "selection_id": "b", "selection": "B", "stake": 10},
    ]}
    now = datetime.now(timezone.utc).isoformat()
    states = {
        ("Betfair delayed", "bf"): {"ok": True, "status": "OPEN", "in_play": True, "captured_at": now, "latency_ms": 5, "quotes": {"a": {"odds": 2.1, "liquidity": 50}}},
        ("Matchbook", "mb"): {"ok": True, "status": "OPEN", "in_play": False, "captured_at": now, "latency_ms": 4, "quotes": {"b": {"odds": 2.1, "liquidity": 50}}},
    }
    result = evaluate_observation(legs, original, states, bankroll=100, max_bankroll_pct=100,
                                  max_event_exposure_pct=100, min_roi=0, min_profit=0, pre_match_only=True)
    assert result["failure_reason"] == "BETFAIR_IN_PLAY"
    bf = next(v for v in result["venues"] if v["exchange"] == "Betfair delayed")
    assert bf["captured_at"] == now
    assert bf["quote_age_seconds"] is not None


def test_frontend_exposes_split_scanner_loops_and_cadence_settings():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "PoC 0.9.36" in html
    assert "Price scan" in html
    assert "Next discovery" in html
    assert 'id="dashDiscoveryState"' in html
    assert 'id="dashDiscoveryScans"' in html
    assert 'id="priceTick"' in html
    assert 'id="quoteMaxAge"' in html
    assert "BETFAIR_IN_PLAY" in html
    assert "let d=executionDiag(x);return d.executed" in html


def test_split_scanner_discovers_once_then_refreshes_cached_prices(tmp_path):
    from arbscanner.models import ExchangeMarket, Quote
    from arbscanner.scanner import Scanner
    from arbscanner.secrets import SecretStore

    now = datetime.now(timezone.utc)
    start = (now + timedelta(hours=1)).isoformat()

    def quote(exchange, eid, mid, sid, selection, odds):
        return Quote(exchange, eid, mid, "Alpha v Beta", "Match Winner", sid, selection, odds, 100,
                     datetime.now(timezone.utc).isoformat(), start, 0.0, "test", 1,
                     "match winner", "two-way", "Tennis", False, "OPEN")

    bf_market = ExchangeMarket("Betfair delayed", "be", "bm", "Alpha v Beta", "Match Winner", start,
                              [quote("Betfair delayed", "be", "bm", "a", "Alpha", 2.2), quote("Betfair delayed", "be", "bm", "b", "Beta", 1.8)],
                              market_type="match winner", strategy="two-way", sport="Tennis", in_play=False)
    mb_market = ExchangeMarket("Matchbook", "me", "mm", "Alpha vs Beta", "Match Winner", start,
                              [quote("Matchbook", "me", "mm", "a2", "Alpha", 1.8), quote("Matchbook", "me", "mm", "b2", "Beta", 2.2)],
                              market_type="match winner", strategy="two-way", sport="Tennis", in_play=False)

    class Fake:
        def __init__(self, name, market): self.name, self.market, self.discovery_calls, self.price_calls = name, market, 0, 0
        async def fetch_markets(self, horizon_hours=24, minimum_liquidity=0): self.discovery_calls += 1; return [self.market]
        async def fetch_market_state(self, event_id, market_id):
            self.price_calls += 1
            return {"ok": True, "exchange": self.name, "event_id": event_id, "market_id": market_id, "status": "OPEN", "in_play": False,
                    "latency_ms": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
                    "quotes": {str(q.selection_id): {"odds": q.odds, "liquidity": q.liquidity} for q in self.market.quotes}}
        async def fetch_market_states(self, requests): return [await self.fetch_market_state(x["event_id"], x["market_id"]) for x in requests]

    db = DB(tmp_path / "split.sqlite3")
    db.set_setting("mode", "live")  # bypass timed SIM Monitor observation; LIVE orders remain absent/read-only.
    # 0.9.36 makes engine SIM/LIVE enablement independent. Explicitly select the
    # baseline engine for LIVE decision evidence rather than inheriting SIM state.
    db.ensure_default_engines()
    db.engine_set_mode_enablement("SPORTS_BASELINE_ARB_PRIMARY", "live", True)
    db.set_setting("config", {"event_match_threshold": .5, "minimum_net_roi_pct": .1, "minimum_profit": 0,
                              "minimum_liquidity": 2, "research_two_way_enabled": True, "quality_reference_bankroll": 100,
                              "execution_max_stake": 25, "price_quote_max_age_seconds": 10, "price_refresh_near_seconds": 2,
                              "price_refresh_today_seconds": 8, "price_refresh_later_seconds": 30})
    bf, mb = Fake("Betfair delayed", bf_market), Fake("Matchbook", mb_market)
    scanner = Scanner(db, SecretStore()); scanner._adapters = lambda mode="sim": [bf, mb]
    discovery = scanner.discover_once()
    assert discovery["matches"] == 1 and db.market_cache_stats()["active"] == 1
    assert bf.discovery_calls == 1 and mb.discovery_calls == 1
    price = scanner.price_scan_once(force=True)
    assert price["pipeline"]["processed"] == 1
    assert price["pipeline"]["opportunities"] == 1
    assert bf.discovery_calls == 1 and mb.discovery_calls == 1  # no full rediscovery
    assert bf.price_calls == 1 and mb.price_calls == 1
    # Nothing is immediately due after the forced refresh, and no empty scan record is written.
    scans_before = db.scan_pipeline_between()["scans"]
    skipped = scanner.price_scan_once(force=False)
    assert skipped.get("skipped") is True
    assert db.scan_pipeline_between()["scans"] == scans_before
