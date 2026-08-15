from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.engine import simulate_equal_return
from arbscanner.models import ExchangeMarket, Leg, Quote, Scenario
from arbscanner.normalization import align_quotes, classify_market, match_markets
from arbscanner.racing import is_withdrawn_status, normalize_track, runner_match_score
from arbscanner.scanner import Scanner
from arbscanner.secrets import SecretStore


def _race_market(exchange: str, event_id: str, market_id: str, start: str, *, reorder=False, missing=False, trap_shift=0):
    names = ["Swift Arrow", "Blue Comet", "Rapid Star", "Night Flyer", "Golden Dash", "Silver Jet"]
    odds = [7.0, 7.2, 7.4, 7.1, 7.3, 7.5]
    rows = list(enumerate(zip(names, odds), start=1))
    if reorder:
        rows = [rows[i] for i in [4, 1, 5, 0, 3, 2]]
    if missing:
        rows = rows[:-1]
    quotes = []
    for trap, (name, odd) in rows:
        quoted_odd = odd + (0.15 if (exchange == "Matchbook" and trap % 2 == 0) else 0.0)
        quoted_odd += (0.10 if (exchange == "Betfair delayed" and trap % 2 == 1) else 0.0)
        quotes.append(Quote(
            exchange=exchange,
            event_id=event_id,
            market_id=market_id,
            event_name="Romford 10th Aug",
            market_name="Win",
            selection_id=f"{exchange[:2]}-{trap}",
            selection=f"{trap}. {name}",
            odds=quoted_odd,
            liquidity=250.0,
            captured_at=datetime.now(timezone.utc).isoformat(),
            start_time=start,
            commission_pct=2.0,
            commission_source="test",
            source_latency_ms=10,
            market_type="win",
            strategy="multi_runner_win",
            sport="Greyhounds",
            in_play=False,
            market_status="OPEN",
            section="racing",
            trap_number=trap + trap_shift,
            canonical_selection_key=f"trap:{trap}|{name.lower().replace(' ', '-')}",
            runner_status="ACTIVE",
        ))
    return ExchangeMarket(
        exchange=exchange,
        event_id=event_id,
        market_id=market_id,
        event_name="Romford 10th Aug",
        market_name="Win",
        start_time=start,
        quotes=quotes,
        status="OPEN",
        market_type="win",
        strategy="multi_runner_win",
        sport="Greyhounds",
        in_play=False,
        section="racing",
        race_track="Romford",
        race_number=8,
    )


def test_six_runner_engine_is_n_selection_capable():
    legs = [
        Leg("Betfair delayed" if i % 2 else "Matchbook", f"Trap {i}", 7.5, 1000.0, 2.0,
            strategy="multi_runner_win", sport="Greyhounds", section="racing", trap_number=i)
        for i in range(1, 7)
    ]
    sim = simulate_equal_return(legs, Scenario("six", 500.0, 100.0, 100.0))
    assert sim["executable"] is True
    assert len(sim["stakes"]) == 6
    assert len(sim["outcome_pnls"]) == 6
    assert min(sim["outcome_pnls"].values()) > 0


def test_greyhound_win_classification_and_place_exclusion():
    assert classify_market("Win", 6, "Greyhounds") == ("win", "multi_runner_win")
    assert classify_market("Race Winner", 6, "Greyhounds") == ("win", "multi_runner_win")
    assert classify_market("Place", 6, "Greyhounds")[1] == "unknown"
    assert classify_market("Forecast", 6, "Greyhounds")[1] == "unknown"


def test_race_and_runner_matching_is_order_independent():
    start = (datetime.now(timezone.utc) + timedelta(minutes=8)).isoformat()
    bf = _race_market("Betfair delayed", "bf-e", "bf-m", start)
    mb = _race_market("Matchbook", "mb-e", "mb-m", start, reorder=True)
    matches = match_markets([bf, mb], threshold=.72, racing_threshold=.90)
    assert len(matches) == 1
    m = matches[0]
    assert m.section == "racing"
    assert m.runner_count == 6
    assert normalize_track(m.race_track) == "romford"
    groups = align_quotes(m, racing_threshold=.92)
    assert len(groups) == 6
    assert all(len(v) == 2 for v in groups.values())
    assert {q.trap_number for group in groups.values() for q in group} == set(range(1, 7))


def test_racing_rejects_incomplete_field_and_bad_trap_alignment():
    start = (datetime.now(timezone.utc) + timedelta(minutes=8)).isoformat()
    bf = _race_market("Betfair delayed", "bf-e", "bf-m", start)
    mb_missing = _race_market("Matchbook", "mb-e", "mb-m", start, missing=True)
    assert match_markets([bf, mb_missing], racing_threshold=.90) == []

    mb_bad = _race_market("Matchbook", "mb-e2", "mb-m2", start)
    # Deliberately corrupt one trap while keeping the same runner name. A strict
    # Racing match now requires the complete runner field to align up front.
    mb_bad.quotes[0] = Quote(**{**asdict(mb_bad.quotes[0]), "trap_number": 6})
    assert match_markets([bf, mb_bad], racing_threshold=.90, racing_runner_threshold=.92) == []


def test_race_time_mismatch_is_not_matched():
    now = datetime.now(timezone.utc)
    bf = _race_market("Betfair delayed", "bf-e", "bf-m", (now + timedelta(minutes=5)).isoformat())
    mb = _race_market("Matchbook", "mb-e", "mb-m", (now + timedelta(minutes=11)).isoformat())
    assert match_markets([bf, mb], racing_threshold=.90) == []


def test_racing_helpers_are_conservative():
    assert runner_match_score("1. Swift Arrow", "1. Swift Arrow", 1, 1) == 1.0
    assert runner_match_score("1. Swift Arrow", "2. Swift Arrow", 1, 2) == 0.0
    assert is_withdrawn_status("REMOVED") is True
    assert is_withdrawn_status("ACTIVE") is False


def test_database_racing_columns_and_rows(tmp_path: Path):
    db = DB(tmp_path / "racing.sqlite3")
    for col in ("section", "race_track", "race_number", "runner_count", "time_to_off_seconds"):
        assert col in db._columns("matched_markets")
    for col in ("section", "trap_number", "canonical_selection_key", "runner_status"):
        assert col in db._columns("snapshots")
    sid = db.start_scan(scan_kind="price")
    db.finish_scan(sid)
    db.add_matched_market(
        sid, "romford|x", "Romford", datetime.now(timezone.utc).isoformat(), "Win", 1.0,
        2.0, 2.0, .4, 1.6, 100.0, 1.6, "nominal", "racing_opportunity", "Research only",
        [], [], strategy="multi_runner_win", sport="Greyhounds", section="racing", race_track="romford",
        race_number=8, runner_count=6, time_to_off_seconds=120, in_play=False, event_status="OPEN",
    )
    row = db.latest_matched_markets()["rows"][0]
    assert row["section"] == "racing"
    assert row["runner_count"] == 6
    assert row["time_to_off_seconds"] == 120


def test_scanner_racing_qualifies_for_monitor_and_opens_virtual_position(tmp_path: Path):
    start = (datetime.now(timezone.utc) + timedelta(minutes=8)).isoformat()
    bf_market = _race_market("Betfair delayed", "bf-e", "bf-m", start)
    mb_market = _race_market("Matchbook", "mb-e", "mb-m", start, reorder=True)

    class Fake:
        def __init__(self, name, market):
            self.name, self.market = name, market
        async def fetch_markets(self, horizon_hours=24, minimum_liquidity=0):
            return [self.market]
        async def fetch_market_state(self, event_id, market_id):
            return {
                "ok": True, "exchange": self.name, "event_id": event_id, "market_id": market_id,
                "status": "OPEN", "in_play": False, "latency_ms": 1,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "quotes": {str(q.selection_id): {"odds": q.odds, "liquidity": q.liquidity} for q in self.market.quotes},
            }
        async def fetch_market_states(self, requests):
            return [await self.fetch_market_state(x["event_id"], x["market_id"]) for x in requests]

    db = DB(tmp_path / "scan.sqlite3")
    db.set_setting("config", {
        "racing_greyhounds_enabled": True,
        "research_multi_runner_enabled": True,
        "racing_match_threshold": .90,
        "racing_runner_match_threshold": .92,
        "racing_minimum_liquidity": 1.0,
        "racing_minimum_net_roi_pct": .01,
        "racing_minimum_profit": 0.0,
        "quality_reference_bankroll": 100.0,
        "require_cross_exchange": True,
        "price_quote_max_age_seconds": 10.0,
        "price_refresh_near_seconds": 2,
        "price_refresh_today_seconds": 8,
        "price_refresh_later_seconds": 30,
    })
    bf, mb = Fake("Betfair delayed", bf_market), Fake("Matchbook", mb_market)
    scanner = Scanner(db, SecretStore())
    scanner._adapters = lambda mode="sim": [bf, mb]
    discovery = scanner.discover_once()
    assert discovery["matches"] == 1
    result = scanner.price_scan_once(force=True)
    assert result["pipeline"]["processed"] == 1
    latest = db.latest_matched_markets()["rows"][0]
    assert latest["section"] == "racing"
    assert latest["status"] == "racing_qualified"
    assert result["pipeline"]["racing_qualified"] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM opportunities WHERE section='racing'").fetchone()[0] == 1
    run = dict(db.conn.execute("SELECT * FROM execution_runs ORDER BY id DESC LIMIT 1").fetchone())
    assert run["execution_type"] == "modeled_racing_monitor"
    assert run["is_real"] == 0
    positions = db.monitor_open_positions("racing")
    assert len(positions) == 1
    assert positions[0]["section"] == "racing"
    assert positions[0]["stream"] == "racing"
    import json
    details = json.loads(run["details_json"] or "{}")
    snap = details["qualification_snapshot"]
    assert snap["monitor_stream"] == "racing"
    assert snap["live_order_placement"] is False
    assert len(snap["legs"]) == 6
    assert len(snap["stakes"]) == 6
    wallets = db.monitor_wallets_by_stream()
    assert sum(x["reserved"] for x in wallets["racing"].values()) > 0
    assert sum(x["reserved"] for x in wallets["pre_match"].values()) == 0
    assert sum(x["reserved"] for x in wallets["in_play"].values()) == 0

    opportunity_id = int(positions[0]["opportunity_id"])
    settlement = db.settle_monitor_position(opportunity_id, "1. Swift Arrow")
    assert settlement and settlement["ok"] is True
    api = API(tmp_path / "scan.sqlite3")
    results = api.settled_positions({"phase": "racing"})
    assert results["summary"]["settled"] == 1
    assert results["rows"][0]["stream"] == "racing"
    assert results["rows"][0]["sport"] == "Greyhounds"
    performance = api.performance_analytics({"period": "7d", "scope": "racing", "basis": "actual"})
    assert performance["summary"]["settled_bets"] == 1


def test_api_racing_overview_exposes_monitor_only_boundary(tmp_path: Path):
    api = API(db_path=tmp_path / "api.sqlite3")
    start = (datetime.now(timezone.utc) + timedelta(minutes=4)).isoformat()
    legs = [asdict(Leg(
        exchange="Betfair delayed" if i % 2 else "Matchbook", selection=f"{i}. Runner {i}", odds=7.5,
        liquidity=100.0, commission_pct=2.0, strategy="multi_runner_win", sport="Greyhounds", section="racing",
        trap_number=i, canonical_selection_key=f"trap:{i}|runner-{i}", runner_status="ACTIVE",
    )) for i in range(1, 7)]
    sid = api.db.start_scan(scan_kind="price")
    api.db.finish_scan(sid)
    api.db.add_matched_market(
        sid, "romford|x", "Romford", start, "Win", 1.0, 20.0, 20.0, 1.0, 19.0,
        100.0, 19.0, "nominal", "racing_opportunity", "Research only", legs, [],
        strategy="multi_runner_win", sport="Greyhounds", section="racing", race_track="romford", race_number=8,
        runner_count=6, time_to_off_seconds=240, in_play=False, event_status="OPEN",
    )
    r = api.racing_overview({})
    assert r["ok"] is True
    assert r["research_only"] is False
    assert r["monitor_execution_allowed"] is True
    assert r["live_execution_allowed"] is False
    assert r["summary"]["matched_races"] == 1
    assert r["rows"][0]["runner_count"] == 6
    assert len(r["rows"][0]["outcome_pnls"]) == 6


def test_frontend_activates_racing_monitor_page():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert "ArbScanner PoC 0.9.36" in html
    assert 'data-tab="racing"' in html
    assert '<section id="racing" class="page">' in html
    assert "MONITOR only" in html
    assert "MONITOR ENABLED · LIVE LOCKED" in html
    assert "LIVE order placement remains hard-locked" in html
    assert "racing_overview" in html
    assert "SOON</span>" not in html
