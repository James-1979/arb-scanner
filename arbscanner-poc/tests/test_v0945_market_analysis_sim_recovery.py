from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.api import API


def _seed_sim_market(api: API) -> tuple[str, str]:
    db = api.db
    scan = db.start_scan(scan_kind="price")
    now = datetime.now(timezone.utc)
    db.add_matched_market(
        scan,
        "evt-recovery-1",
        "Recovery United v Integrity City",
        (now + timedelta(hours=2)).isoformat(),
        "Match Odds",
        0.99,
        2.5,
        2.5,
        0.1,
        2.4,
        125.0,
        3.0,
        "liquidity",
        "recommended",
        "SIM recovery fixture",
        [
            {"venue_id": "betfair", "selection": "HOME", "side": "BACK", "odds": 2.2, "liquidity": 500.0},
            {"venue_id": "matchbook", "selection": "AWAY", "side": "BACK", "odds": 2.2, "liquidity": 450.0},
        ],
        [],
        sport="Football",
        section="sports",
        in_play=False,
        liquidity_capable=True,
        max_executable_stake=125.0,
    )
    return (now - timedelta(hours=1)).isoformat(), (now + timedelta(hours=1)).isoformat()


def test_0945_orphan_history_marker_does_not_hide_raw_sim_market_analysis(tmp_path: Path):
    api = API(tmp_path / "market-recovery.sqlite3")
    start, finish = _seed_sim_market(api)

    before = api.market_analysis({
        "mode": "sim", "from_utc": start, "to_utc": finish,
        "scope": "all", "phase": "all", "sport": "all",
        "timezone_name": "UTC", "timezone_offset_minutes": 0,
    })
    assert before["ok"] is True
    assert before["rows"]

    # Reproduce the mature/upgraded DB failure: the retention/finalisation marker
    # survives but one or more compact Market Analysis structures are absent.
    # Raw hot evidence is still present and must be used instead of being hidden.
    with api.db.lock:
        assert api.db.conn.execute("SELECT COUNT(*) FROM matched_market_history_state").fetchone()[0] > 0
        assert api.db.conn.execute("SELECT COUNT(*) FROM matched_markets").fetchone()[0] > 0
        api.db.conn.execute("DELETE FROM market_hourly_rollups")
        api.db.conn.execute("DELETE FROM market_hourly_seen")
        api.db.conn.execute("DELETE FROM matched_market_reason_hourly_rollups")
        api.db.conn.execute("DELETE FROM liquidity_opportunity_hourly_rollups")
        api.db.conn.commit()

    recovered = api.market_analysis({
        "mode": "sim", "from_utc": start, "to_utc": finish,
        "scope": "all", "phase": "all", "sport": "all",
        "timezone_name": "UTC", "timezone_offset_minutes": 0,
    })
    assert recovered["ok"] is True
    assert len(recovered["rows"]) == 1
    row = recovered["rows"][0]
    assert row["sport"] == "Football"
    assert row["market_name"] == "Match Odds"
    assert row["observations"] >= 1
    assert row["unique_markets"] == 1
    assert row["net_positive"] == 1
    assert recovered["liquidity_funnel"]["observed"] >= 1
    assert recovered["liquidity_funnel"]["positive"] >= 1
    assert recovered["liquidity_funnel"]["liquidity_capable"] >= 1


def test_0945_frontend_surfaces_sim_market_read_failure_and_uses_bounded_recovery_timeout():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "function renderMarketAnalysisFailure0945" in html
    assert "Market Analysis could not load." in html
    assert "timeoutMs:20000" in html
    assert "if(!r?.ok){renderMarketAnalysisFailure0945(r);return r}" in html


def test_0945_legacy_unfinalized_hour_still_prefers_raw_without_double_count(tmp_path: Path):
    api = API(tmp_path / "market-legacy-source.sqlite3")
    start, finish = _seed_sim_market(api)
    # Simulate a legacy lazy-rollup hour: compact rows exist, but the authoritative
    # finalisation ledger does not own the hour yet. Existing semantics must remain
    # raw-first and must not count the same observation twice.
    with api.db.lock:
        api.db.conn.execute("DELETE FROM matched_market_history_state")
        api.db.conn.commit()
    result = api.market_analysis({
        "mode": "sim", "from_utc": start, "to_utc": finish,
        "scope": "all", "phase": "all", "sport": "all",
        "timezone_name": "UTC", "timezone_offset_minutes": 0,
    })
    assert result["ok"] is True
    assert len(result["rows"]) == 1
    assert result["rows"][0]["observations"] == 1
    assert result["rows"][0]["unique_markets"] == 1


def test_0945_release_identity_and_installer_lock():
    from arbscanner import __version__

    root = Path(__file__).parents[1]
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    installer = (root / "BUILD_AND_INSTALL.command").read_text(encoding="utf-8")
    assert __version__ == "0.9.45"
    assert "<title>ArbScanner PoC 0.9.45</title>" in html
    assert 'EXPECTED_VERSION="0.9.45"' in installer
