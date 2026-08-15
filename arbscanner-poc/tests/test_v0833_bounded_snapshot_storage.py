from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from arbscanner.db import DB


def row(i: int = 1, *, odds: float = 2.0, exchange: str = "Betfair delayed") -> dict:
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "exchange": exchange,
        "event_id": "e1",
        "event_name": "Alpha v Beta",
        "market_id": "m1",
        "market_name": "Match Odds",
        "selection_id": "s1",
        "selection": "Alpha",
        "side": "back",
        "odds": odds,
        "liquidity": 50 + i,
        "source_latency_ms": 10,
        "commission_pct": 2.0,
        "commission_source": "configured",
        "market_type": "match odds",
        "strategy": "1x2",
        "sport": "Football",
        "in_play": 0,
        "market_status": "OPEN",
        "section": "sports",
        "raw_json": "{}",
    }


def test_latest_quote_store_is_bounded_and_rolls_up_observations(tmp_path):
    db = DB(tmp_path / "bounded.sqlite3")
    assert db.upsert_latest_snapshots([row(1, odds=2.0)])["rows"] == 1
    assert db.upsert_latest_snapshots([row(2, odds=2.2)])["rows"] == 1

    current = db.conn.execute("SELECT odds,liquidity FROM latest_snapshots").fetchall()
    assert len(current) == 1
    assert current[0]["odds"] == 2.2
    assert db.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
    assert db.conn.execute("SELECT SUM(quote_observations) FROM snapshot_rollups").fetchone()[0] == 2


def test_legacy_raw_snapshots_are_pruned_in_batches_without_touching_current_store(tmp_path):
    db = DB(tmp_path / "prune.sqlite3")
    db.add_snapshots([row(i, odds=2.0 + i / 10000) for i in range(1200)])
    db.upsert_latest_snapshots([row(9999, odds=3.0)])

    result = db.snapshot_storage_maintenance(keep_legacy_rows=100, batch_size=5000)
    assert result["deleted"] == 1100
    assert result["done"] is True
    assert db.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 100
    assert db.conn.execute("SELECT COUNT(*) FROM latest_snapshots").fetchone()[0] == 1


def test_v0833_additive_storage_upgrade_does_not_require_full_historical_migration(tmp_path, monkeypatch):
    path = tmp_path / "upgrade.sqlite3"
    db = DB(path)
    db.conn.close()
    conn = sqlite3.connect(path)
    for name in ("latest_snapshots", "snapshot_rollups", "snapshot_storage_state"):
        conn.execute(f"DROP TABLE {name}")
    conn.commit(); conn.close()

    def fail_if_called(self):
        raise AssertionError("full historical migration should not run for an otherwise-current 0.9.0 database")

    monkeypatch.setattr(DB, "_migrate", fail_if_called)
    upgraded = DB(path)
    names = {r[0] for r in upgraded.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"latest_snapshots", "snapshot_rollups", "snapshot_storage_state"}.issubset(names)


def test_storage_health_reports_reusable_pages_and_latest_rows(tmp_path):
    db = DB(tmp_path / "health.sqlite3")
    db.upsert_latest_snapshots([row(1), row(2, exchange="Matchbook")])
    health = db.snapshot_storage_health()
    assert health["mode"] == "bounded_latest"
    assert {x["exchange"] for x in health["latest"]} == {"Betfair delayed", "Matchbook"}
    assert health["legacy_rows_remaining_estimate"] == 0


def test_snapshot_write_failure_does_not_erase_exchange_connectivity(tmp_path):
    from datetime import timedelta
    from arbscanner.models import ExchangeMarket, Quote
    from arbscanner.scanner import Scanner
    from arbscanner.secrets import SecretStore

    start = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    def q(exchange, eid, mid, sid, selection, odds):
        return Quote(exchange, eid, mid, "Alpha v Beta", "Match Winner", sid, selection, odds, 100,
                     datetime.now(timezone.utc).isoformat(), start, 0.0, "test", 1,
                     "match winner", "two-way", "Tennis", False, "OPEN")

    bf_market = ExchangeMarket("Betfair delayed", "be", "bm", "Alpha v Beta", "Match Winner", start,
        [q("Betfair delayed", "be", "bm", "a", "Alpha", 2.2), q("Betfair delayed", "be", "bm", "b", "Beta", 1.8)],
        market_type="match winner", strategy="two-way", sport="Tennis", in_play=False)
    mb_market = ExchangeMarket("Matchbook", "me", "mm", "Alpha v Beta", "Match Winner", start,
        [q("Matchbook", "me", "mm", "a2", "Alpha", 1.8), q("Matchbook", "me", "mm", "b2", "Beta", 2.2)],
        market_type="match winner", strategy="two-way", sport="Tennis", in_play=False)

    class Fake:
        def __init__(self, name, market): self.name, self.market = name, market
        async def fetch_markets(self, horizon_hours=24, minimum_liquidity=0): return [self.market]
        async def fetch_market_state(self, event_id, market_id):
            return {"ok": True, "exchange": self.name, "event_id": event_id, "market_id": market_id,
                    "status": "OPEN", "in_play": False, "latency_ms": 1,
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "quotes": {str(x.selection_id): {"odds": x.odds, "liquidity": x.liquidity} for x in self.market.quotes}}
        async def fetch_market_states(self, requests):
            return [await self.fetch_market_state(x["event_id"], x["market_id"]) for x in requests]

    db = DB(tmp_path / "storage-failure.sqlite3")
    db.set_setting("mode", "monitor")
    db.set_setting("config", {"event_match_threshold": .5, "minimum_net_roi_pct": 99, "minimum_profit": 0,
                              "minimum_liquidity": 2, "research_two_way_enabled": True,
                              "quality_reference_bankroll": 100, "execution_max_stake": 25,
                              "price_quote_max_age_seconds": 10, "price_refresh_near_seconds": 2,
                              "price_refresh_today_seconds": 8, "price_refresh_later_seconds": 30})
    scanner = Scanner(db, SecretStore())
    scanner._adapters = lambda mode="sim": [Fake("Betfair delayed", bf_market), Fake("Matchbook", mb_market)]
    discovery = scanner.discover_once()
    assert discovery["ok"] is True

    def fail_storage(_markets):
        raise sqlite3.OperationalError("simulated snapshot storage failure")

    scanner._persist_snapshots = fail_storage
    result = scanner.price_scan_once(force=True)
    assert result["ok"] is True
    assert len(result["statuses"]) == 2
    assert all(x["ok"] for x in result["statuses"])
    assert "simulated snapshot storage failure" in result["stage_timings"]["snapshot_write_error"]
    last = db.conn.execute("SELECT status_json,error FROM scan_runs WHERE scan_kind='price' ORDER BY id DESC LIMIT 1").fetchone()
    assert last["error"] is None
    assert "Betfair delayed" in last["status_json"] and "Matchbook" in last["status_json"]
