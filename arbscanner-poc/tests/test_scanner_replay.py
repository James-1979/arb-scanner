import json
from datetime import datetime, timezone
from pathlib import Path
from arbscanner.db import DB
from arbscanner.models import ExchangeMarket, Quote
from arbscanner.replay import replay_scenarios
from arbscanner.scanner import Scanner
from arbscanner.secrets import SecretStore


class FakeAdapter:
    def __init__(self, name, markets):
        self.name = name
        self.markets = markets
    async def fetch_markets(self, horizon_hours=24, minimum_liquidity=2.0):
        return self.markets
    async def fetch_market_state(self, event_id: str, market_id: str):
        market = next(m for m in self.markets if str(m.market_id) == str(market_id))
        return {
            "ok": True, "exchange": self.name, "event_id": str(event_id), "market_id": str(market_id),
            "status": "OPEN", "in_play": False, "latency_ms": 1, "captured_at": "2026-08-09T12:00:00+00:00",
            "quotes": {str(q.selection_id): {"odds": q.odds, "liquidity": q.liquidity} for q in market.quotes},
        }


def mk_quote(ex, eid, mid, event, sid, sel, odds, liq):
    return Quote(ex, eid, mid, event, "Match Odds", sid, sel, odds, liq, "2026-08-09T12:00:00+00:00", "2026-08-09T15:00:00Z", 2.0)


def make_markets():
    event_a = "Northbridge v Riverside"
    event_b = "Northbridge vs Riverside"
    a = ExchangeMarket("Betfair delayed", "bf-e", "bf-m", event_a, "Match Odds", "2026-08-09T15:00:00Z", [
        mk_quote("Betfair delayed", "bf-e", "bf-m", event_a, "1", "Northbridge", 2.65, 500),
        mk_quote("Betfair delayed", "bf-e", "bf-m", event_a, "2", "Draw", 3.75, 265),
        mk_quote("Betfair delayed", "bf-e", "bf-m", event_a, "3", "Riverside", 3.05, 180),
    ])
    b = ExchangeMarket("Matchbook", "mb-e", "mb-m", event_b, "1X2", "2026-08-09T15:01:00Z", [
        mk_quote("Matchbook", "mb-e", "mb-m", event_b, "4", "Northbridge", 2.72, 420),
        mk_quote("Matchbook", "mb-e", "mb-m", event_b, "5", "The Draw", 3.60, 300),
        mk_quote("Matchbook", "mb-e", "mb-m", event_b, "6", "Riverside", 2.95, 300),
    ])
    return a, b


def test_scan_persists_and_deduplicates(tmp_path: Path):
    db = DB(tmp_path / "test.sqlite3")
    db.set_setting("scenarios", [500, 1000, 5000])
    db.set_setting("config", {
        "horizon_hours": 24,
        "minimum_liquidity": 2,
        "event_match_threshold": 0.55,
        "minimum_net_roi_pct": 0.1,
        "max_bankroll_pct": 100,
        "max_event_exposure_pct": 100,
        "one_recommendation_per_market": True,
    })
    a, b = make_markets()
    scanner = Scanner(db, SecretStore())
    scanner._adapters = lambda mode="sim": [FakeAdapter("Betfair delayed", [a]), FakeAdapter("Matchbook", [b])]
    first = scanner.scan_once()
    assert first["ok"] is True
    assert len(first["found"]) == 1
    assert db.dashboard()["snapshots"] == 6
    assert db.dashboard()["opportunities"] == 1
    second = scanner.scan_once()
    assert second["ok"] is True
    assert len(second["found"]) == 0
    assert db.dashboard()["opportunities"] == 1


def test_replay_respects_capital_and_liquidity(tmp_path: Path):
    db = DB(tmp_path / "replay.sqlite3")
    db.set_setting("scenarios", [500, 50000])
    a, b = make_markets()
    scanner = Scanner(db, SecretStore())
    db.set_setting("config", {
        "horizon_hours": 24, "minimum_liquidity": 2, "event_match_threshold": 0.55,
        "minimum_net_roi_pct": 0.1, "max_bankroll_pct": 100, "max_event_exposure_pct": 100,
        "one_recommendation_per_market": True,
    })
    scanner._adapters = lambda mode="sim": [FakeAdapter("Betfair delayed", [a]), FakeAdapter("Matchbook", [b])]
    scanner.scan_once()
    results = replay_scenarios(db, [500, 50000], 100)
    assert len(results) == 2
    assert results[0]["deployed_total"] <= 500
    assert results[1]["deployed_total"] < 50000
    assert results[1]["liquidity_limited"] >= 1


def test_latest_scan_records_matched_market_diagnostics(tmp_path: Path):
    db = DB(tmp_path / "matched.sqlite3")
    db.set_setting("scenarios", [500])
    db.set_setting("config", {
        "horizon_hours": 24,
        "minimum_liquidity": 2,
        "event_match_threshold": 0.55,
        "minimum_net_roi_pct": 0.1,
        "max_bankroll_pct": 100,
        "max_event_exposure_pct": 100,
        "one_recommendation_per_market": True,
        "require_cross_exchange": True,
    })
    a, b = make_markets()
    scanner = Scanner(db, SecretStore())
    scanner._adapters = lambda mode="sim": [FakeAdapter("Betfair delayed", [a]), FakeAdapter("Matchbook", [b])]
    result = scanner.scan_once()
    assert result["ok"] is True
    matched = db.latest_matched_markets()
    assert matched["summary"]["matched"] == 1
    assert len(matched["rows"]) == 1
    row = matched["rows"][0]
    assert row["event_name"] == "Northbridge v Riverside"
    assert row["net_roi_pct"] is not None
    assert len({l["exchange"] for l in row["legs"]}) >= 2


def test_scenario_rows_expose_bankroll_roi(tmp_path: Path):
    db = DB(tmp_path / "roi.sqlite3")
    db.set_setting("scenarios", [500])
    db.set_setting("config", {
        "horizon_hours": 24, "minimum_liquidity": 2, "event_match_threshold": 0.55,
        "minimum_net_roi_pct": 0.1, "max_bankroll_pct": 100, "max_event_exposure_pct": 100,
        "one_recommendation_per_market": True, "require_cross_exchange": True,
    })
    a, b = make_markets()
    scanner = Scanner(db, SecretStore())
    scanner._adapters = lambda mode="sim": [FakeAdapter("Betfair delayed", [a]), FakeAdapter("Matchbook", [b])]
    result = scanner.scan_once()
    oid = result["found"][0]["id"]
    row = db.scenario_runs_for_opportunity(oid)[0]
    assert row["capital_used_pct"] > 0
    assert row["bankroll_roi_pct"] > 0
    assert row["bankroll_roi_pct"] <= row["expected_roi_pct"]


def test_monitor_timing_mode_attaches_paper_execution_summary(tmp_path: Path):
    db = DB(tmp_path / "monitor_timing.sqlite3")
    db.set_setting("mode", "monitor_timing")
    db.set_setting("scenarios", [500])
    db.set_setting("config", {
        "horizon_hours": 24, "minimum_liquidity": 2, "event_match_threshold": 0.55,
        "minimum_net_roi_pct": 0.1, "max_bankroll_pct": 100, "max_event_exposure_pct": 100,
        "one_recommendation_per_market": True, "require_cross_exchange": True,
        "quality_reference_bankroll": 500,
        "execution_plan_ttl_ms": 1500, "execution_max_slippage_pct": 0.5,
        "execution_max_unhedged_exposure": 25, "execution_hedge_reserve_pct": 20,
        "monitor_timing_checkpoints_ms": [1, 2, 3, 4], "monitor_timing_reference_checkpoint_ms": 2, "execution_pre_match_only": False,
    })
    a, b = make_markets()
    scanner = Scanner(db, SecretStore())
    scanner._adapters = lambda mode="sim": [FakeAdapter("Betfair delayed", [a]), FakeAdapter("Matchbook", [b])]
    result = scanner.scan_once()
    assert result["ok"] is True
    assert len(result["found"]) == 1
    monitor_timing = result["found"][0]["monitor_timing_execution"]
    assert monitor_timing["reference_checkpoint_ms"] == 2
    assert monitor_timing["reference_executable"] is True
    assert len(monitor_timing["observations"]) == 4
    journal = db.execution_history(mode="monitor")
    assert len(journal) == 1
    assert journal[0]["execution_type"] == "modeled_monitor"
    assert journal[0]["is_real"] == 0
