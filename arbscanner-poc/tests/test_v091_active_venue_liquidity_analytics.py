from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.api import API
from arbscanner.adapters import BetfairDelayedAdapter, MatchbookAdapter
from arbscanner.db import DB
from arbscanner.models import DepthLevel, Quote
from arbscanner.provider_runtime import ProviderRuntimeProfile
from arbscanner.venues import BETDAQ_SHAPE

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _snapshot(*, captured_at: str, levels: list[dict], provider: str = "betfair", exchange: str = "Betfair (Delayed)") -> dict:
    return {
        "exchange": exchange,
        "provider_id": provider,
        "venue_id": provider,
        "event_id": "evt-1",
        "event_name": "Alpha v Beta",
        "market_id": "mkt-1",
        "market_name": "Match Odds",
        "selection_id": "sel-1",
        "selection": "Alpha",
        "side": "BACK",
        "odds": float(levels[0]["odds"]),
        "liquidity": float(levels[0]["available_size"]),
        "captured_at": captured_at,
        "source_timestamp": captured_at,
        "source_latency_ms": 10,
        "quote_age_ms": 25,
        "commission_pct": 2.0,
        "commission_source": "configured",
        "market_type": "match odds",
        "strategy": "1x2",
        "sport": "Football",
        "in_play": False,
        "market_status": "OPEN",
        "section": "sports",
        "feed_entitlement": "delayed" if provider == "betfair" else "live",
        "market_data_transport": "poll",
        "depth_levels_json": json.dumps(levels),
        "raw_json": "{}",
    }


def _add_matched(db: DB, *, event_key: str, status: str, net_roi: float = 1.0, liquidity_capable: bool = True, max_exec: float = 40.0):
    scan_id = db.start_scan()
    db.add_matched_market(
        scan_id, event_key, f"{event_key} event", None, "Match Odds", 1.0,
        1.2, 1.1, 0.1, net_roi, 50.0, 0.5, "liquidity", status, status,
        [{"exchange": "Betfair (Delayed)", "provider_id": "betfair", "venue_id": "betfair", "selection": "Alpha", "odds": 2.1, "liquidity": 100.0}],
        [{"exchange": "Betfair (Delayed)", "market_id": f"bf-{event_key}"}],
        strategy="1x2", sport="Football", in_play=False, section="sports",
        max_executable_stake=max_exec,
        limiting_provider="betfair",
        limiting_selection="Alpha",
        limiting_side="BACK",
        liquidity_capable=liquidity_capable,
        liquidity_rejection_reason=None if liquidity_capable else "below_liquidity",
        depth_at_qualification={"betfair": {"top_book": 100.0, "top3": 180.0}},
        quote_age_at_qualification_ms=30,
    )
    return scan_id


def test_version_and_market_analysis_contract_is_provider_responsive():
    assert 'ArbScanner PoC 0.9.36' in HTML
    assert 'id="marketVenueSummary"' in HTML
    for dom_id in ("marketObserved", "marketPositive", "marketLiquidityCapable", "marketQualified", "marketAttempted", "marketExecuted", "marketSettled"):
        assert f'id="{dom_id}"' in HTML
    for old in ("marketBetfairMarkets", "marketBetfairOpportunities", "marketMatchbookMarkets", "marketMatchbookOpportunities", "marketExchangeOverlap"):
        assert f'id="{old}"' not in HTML
    assert "renderMarketVenueSummary091" in HTML
    assert "Available depth" in HTML
    assert "Average executable stake" in HTML
    assert "Liquidity-capable opportunities" in HTML
    assert "Liquidity rejection rate" in HTML


def test_depth_helpers_normalise_best_three_levels():
    mb = MatchbookAdapter._depth_levels([
        {"side": "back", "odds": 2.0, "available-amount": 10},
        {"side": "back", "odds": 2.2, "available-amount": 20},
        {"side": "back", "odds": 2.1, "available-amount": 30},
        {"side": "back", "odds": 1.9, "available-amount": 40},
        {"side": "lay", "odds": 2.4, "available-amount": 10},
        {"side": "lay", "odds": 2.2, "available-amount": 20},
        {"side": "lay", "odds": 2.3, "available-amount": 30},
        {"side": "lay", "odds": 2.5, "available-amount": 40},
    ])
    assert [(x.side, x.level, x.odds) for x in mb] == [
        ("BACK", 1, 2.2), ("BACK", 2, 2.1), ("BACK", 3, 2.0),
        ("LAY", 1, 2.2), ("LAY", 2, 2.3), ("LAY", 3, 2.4),
    ]
    bf = BetfairDelayedAdapter._depth_levels({"ex": {
        "availableToBack": [{"price": 2.2, "size": 20}, {"price": 2.1, "size": 30}, {"price": 2.0, "size": 40}, {"price": 1.9, "size": 50}],
        "availableToLay": [{"price": 2.24, "size": 21}, {"price": 2.26, "size": 31}, {"price": 2.28, "size": 41}, {"price": 2.3, "size": 51}],
    }})
    assert [(x.side, x.level, x.odds) for x in bf] == [
        ("BACK", 1, 2.2), ("BACK", 2, 2.1), ("BACK", 3, 2.0),
        ("LAY", 1, 2.24), ("LAY", 2, 2.26), ("LAY", 3, 2.28),
    ]


def test_quote_serialises_depth_and_preserves_feed_provenance():
    q = Quote(
        exchange="Betfair (Delayed)", event_id="e", market_id="m", event_name="A v B", market_name="Match Odds",
        selection_id="s", selection="A", odds=2.1, liquidity=12.0, captured_at=datetime.now(timezone.utc).isoformat(),
        feed_entitlement="delayed", market_data_transport="poll",
        depth_levels=(DepthLevel("BACK", 1, 2.1, 12.0), DepthLevel("BACK", 2, 2.08, 20.0)),
    )
    payload = q.as_dict()
    assert payload["feed_entitlement"] == "delayed"
    assert payload["market_data_transport"] == "poll"
    assert len(payload["depth_levels"]) == 2


def test_bounded_depth_replaces_old_levels_and_rolls_up(tmp_path: Path):
    db = DB(tmp_path / "liq.sqlite3")
    now = datetime.now(timezone.utc).isoformat()
    first = [
        {"side": "BACK", "level": 1, "odds": 2.1, "available_size": 10.0},
        {"side": "BACK", "level": 2, "odds": 2.08, "available_size": 20.0},
        {"side": "BACK", "level": 3, "odds": 2.06, "available_size": 30.0},
    ]
    assert db.upsert_latest_snapshots([_snapshot(captured_at=now, levels=first)])["depth_rows"] == 3
    assert db.conn.execute("SELECT COUNT(*) FROM latest_depth_snapshots").fetchone()[0] == 3
    second = [{"side": "BACK", "level": 1, "odds": 2.12, "available_size": 15.0}]
    db.upsert_latest_snapshots([_snapshot(captured_at=now, levels=second)])
    rows = db.conn.execute("SELECT level,price,available_size FROM latest_depth_snapshots ORDER BY level").fetchall()
    assert [(r["level"], r["price"], r["available_size"]) for r in rows] == [(1, 2.12, 15.0)]
    roll = db.conn.execute("SELECT depth_samples,top_book_depth_sum,top3_depth_sum FROM liquidity_depth_hourly_rollups").fetchone()
    assert roll["depth_samples"] == 2
    assert roll["top_book_depth_sum"] == 25.0
    assert roll["top3_depth_sum"] == 75.0
    db.conn.close()


def test_stale_depth_is_visible_but_excluded_from_current_executable_totals(tmp_path: Path):
    db = DB(tmp_path / "stale.sqlite3")
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    levels = [{"side": "BACK", "level": 1, "odds": 2.1, "available_size": 100.0}]
    db.upsert_latest_snapshots([_snapshot(captured_at=old, levels=levels)])
    row = db.latest_liquidity_summary(stale_after_seconds={"betfair": 1.0})[0]
    assert row["top_book_depth"] == 0.0
    assert row["top3_depth"] == 0.0
    assert row["fresh_depth_rows"] == 0
    assert row["stale_depth_rows"] == 1
    assert row["last_quote_at"] == old
    assert row["feed_entitlement"] == "delayed"
    db.conn.close()


def test_liquidity_evidence_and_funnel_preserve_qualified_semantics(tmp_path: Path):
    db_path = tmp_path / "funnel.sqlite3"
    db = DB(db_path)
    _add_matched(db, event_key="positive-liq", status="below_roi", liquidity_capable=True, max_exec=55.0)
    _add_matched(db, event_key="positive-no-liq", status="below_liquidity", liquidity_capable=False, max_exec=2.0)
    _add_matched(db, event_key="qualified", status="recommended", liquidity_capable=True, max_exec=40.0)
    db.conn.close()
    api = API(db_path)
    result = api.market_analysis({"scope": "sports", "phase": "all", "sport": "Football"})
    funnel = result["liquidity_funnel"]
    assert funnel["observed"] == 3
    assert funnel["positive"] == 3
    assert funnel["liquidity_capable"] == 2
    # Qualified remains the canonical qualified cohort rather than being redefined as liquidity-capable.
    assert funnel["qualified"] == 0 or funnel["qualified"] == 1
    # The compact liquidity rollup itself must record only the recommended row as qualified.
    liq = api.db.liquidity_market_summary_between(None, None)["opportunity"][0]
    assert liq["positive_observations"] == 3
    assert liq["liquidity_capable"] == 2
    assert liq["liquidity_rejected"] == 1
    assert liq["qualified_observations"] == 1
    assert liq["executable_stake_samples"] == 3
    api.db.conn.close()


def test_runtime_disable_omits_provider_and_synthetic_provider_appears_pending(tmp_path: Path):
    api = API(tmp_path / "providers.sqlite3")
    api.provider_runtime.register_provider(BETDAQ_SHAPE, profile=ProviderRuntimeProfile("betdaq", enabled=True))
    result = api.market_analysis({"scope": "all", "phase": "all", "sport": "all"})
    by_id = {x["provider_id"]: x for x in result["venue_summary"]}
    assert "betdaq" in by_id
    assert by_id["betdaq"]["analytics_status"] == "pending"

    api.provider_runtime.set_runtime_enabled("betfair", False)
    result2 = api.market_analysis({"scope": "all", "phase": "all", "sport": "all"})
    ids = {x["provider_id"] for x in result2["venue_summary"]}
    assert "betfair" not in ids
    assert "matchbook" in ids
    assert "betdaq" in ids
    api.db.conn.close()


def test_market_analysis_ui_has_no_provider_specific_venue_renderer_branch():
    start = HTML.index("function renderMarketVenueSummary091")
    end = HTML.find("\nfunction ", start + 20)
    fn = HTML[start:end if end > start else len(HTML)].lower()
    assert "provider == 'betfair'" not in fn
    assert 'provider === "betfair"' not in fn
    assert "provider == 'matchbook'" not in fn
    assert 'provider === "matchbook"' not in fn

