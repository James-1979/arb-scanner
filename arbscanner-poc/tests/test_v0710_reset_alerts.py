from datetime import datetime, timezone
from pathlib import Path

from arbscanner.db import DB
from arbscanner.alerts import qualifies_for_alert


def test_trading_data_reset_clears_every_derived_table(tmp_path):
    db = DB(tmp_path / "arb.sqlite3")
    now = datetime.now(timezone.utc).isoformat()
    with db.lock:
        db.conn.execute("INSERT INTO scan_runs(started_at) VALUES(?)", (now,))
        db.conn.execute(
            "INSERT INTO alert_attempts(track_key,quality_band,quality_score,attempted_at,success,reason) VALUES(?,?,?,?,?,?)",
            ("t", "Strong", 80, now, 0, "test"),
        )
        db.conn.execute(
            "INSERT INTO alert_log(track_key,quality_band,quality_score,sent_at) VALUES(?,?,?,?)",
            ("t", "Strong", 80, now),
        )
        db.conn.execute(
            "INSERT INTO monitor_wallets(exchange,opening_balance,available_balance,reserved_balance,realized_pnl,updated_at) VALUES(?,?,?,?,?,?)",
            ("betfair", 250, 250, 0, 0, now),
        )
        db.conn.commit()
    before = db.trading_data_counts()
    assert before["scan_runs"] == 1
    assert before["alert_attempts"] == 1
    assert before["alert_log"] == 1
    assert before["monitor_wallets"] == 1
    remaining = db.clear_research_history()
    assert all(v == 0 for v in remaining.values())
    assert all(v == 0 for v in db.trading_data_counts().values())


def test_alert_attempts_distinguish_failed_delivery_from_success(tmp_path):
    db = DB(tmp_path / "arb.sqlite3")
    db.record_alert_attempt("track", "Strong", 75, False, "blocked")
    assert db.recent_failed_alert_attempt("track", "Strong", within_minutes=15)
    d = db.alert_diagnostics()
    assert d["attempts_24h"] == 1
    assert d["successes_24h"] == 0
    assert d["last_attempt"]["success"] == 0
    db.record_alert_attempt("track", "Strong", 80, True, "accepted")
    d = db.alert_diagnostics()
    assert d["attempts_24h"] == 2
    assert d["successes_24h"] == 1
    assert d["last_success"]["reason"] == "accepted"


def test_alert_thresholds_still_evaluate_monitor_profile():
    cfg = {
        "alerts_enabled": True,
        "alert_quality_bands": ["Strong", "Excellent"],
        "alert_min_deployed_roi_pct": 0.75,
        "alert_min_bankroll_roi_pct": 0.20,
        "alert_min_capital_used_pct": 20,
        "alert_min_profit": 1,
    }
    profile = {
        "quality_band": "Strong",
        "deployed_roi_pct": 3,
        "bankroll_roi_pct": 1,
        "capital_used_pct": 40,
        "expected_profit": 5,
    }
    assert qualifies_for_alert(profile, cfg) == (True, "passed")


def test_api_reset_archives_and_recreates_only_monitor_wallets(tmp_path, monkeypatch):
    import arbscanner.api as api_module

    monkeypatch.setattr(api_module, "APP_DIR", tmp_path)
    api = api_module.API(tmp_path / "live.sqlite3")
    now = datetime.now(timezone.utc).isoformat()
    with api.db.lock:
        api.db.conn.execute("INSERT INTO scan_runs(started_at) VALUES(?)", (now,))
        api.db.conn.execute(
            "INSERT INTO alert_attempts(track_key,quality_band,quality_score,attempted_at,success,reason) VALUES(?,?,?,?,?,?)",
            ("t", "Strong", 80, now, 1, "accepted"),
        )
        api.db.conn.commit()
    result = api.reset_trading_data()
    assert result["ok"] is True
    assert Path(result["archive"]).exists()
    counts = api.db.trading_data_counts()
    assert counts["monitor_wallets"] == 2
    assert counts["monitor_stream_wallets"] == 6
    assert all(v == 0 for k, v in counts.items() if k not in {"monitor_wallets", "monitor_stream_wallets"})


def test_frontend_exposes_reset_and_alert_diagnostics():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "Reset trading data" in html
    assert "Test notification" in html
    assert "loadAlertStatus" in html
    assert "reset_trading_data" in html


def test_old_default_alert_rules_migrate_to_reachable_monitor_defaults(tmp_path):
    from arbscanner.api import API, DEFAULT_CONFIG

    db = DB(tmp_path / "arb.sqlite3")
    old = dict(DEFAULT_CONFIG)
    old["alert_quality_bands"] = ["Strong", "Excellent"]
    old["alert_min_bankroll_roi_pct"] = 0.20
    old["alert_min_capital_used_pct"] = 20.0
    db.set_setting("config", old)
    db.conn.close()

    api = API(tmp_path / "arb.sqlite3")
    cfg = api.db.get_setting("config", {})
    assert cfg["alert_quality_bands"] == ["Usable", "Strong", "Excellent"]
    assert cfg["alert_min_bankroll_roi_pct"] == 0.0
    assert cfg["alert_min_capital_used_pct"] == 0.0
