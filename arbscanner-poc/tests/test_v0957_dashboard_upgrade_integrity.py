from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from arbscanner import __version__
from arbscanner.api import API
from arbscanner.db import DB

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
NOTES = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")
INSTALLER = (ROOT / "BUILD_AND_INSTALL.command").read_text(encoding="utf-8")


def _downgrade_0956_schema(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("DROP TABLE IF EXISTS settlement_audits")
    columns = {row[1] for row in con.execute("PRAGMA table_info(opportunities)")}
    if "routing_diagnostics_json" in columns:
        con.execute("ALTER TABLE opportunities DROP COLUMN routing_diagnostics_json")
    con.commit()
    con.close()


def test_v0957_release_identity():
    assert __version__ == "0.9.57"
    assert '<title>ArbScanner PoC 0.9.57</title>' in HTML
    assert 'EXPECTED_VERSION="0.9.57"' in INSTALLER
    assert "## 0.9.57 — Dashboard Upgrade & Mode Integrity Closure" in NOTES


def test_mature_database_gets_0956_additive_schema_before_current_schema_fast_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "mature-0955.sqlite3"
    api = API(path)
    api.db.conn.close()
    _downgrade_0956_schema(path)

    # The repair must be targeted. Reopening an otherwise-current mature DB must
    # not fall through to the expensive historical full migration path.
    monkeypatch.setattr(DB, "_migrate", lambda self: (_ for _ in ()).throw(AssertionError("full migration should not run")))
    reopened = API(path)

    columns = {row[1] for row in reopened.db.conn.execute("PRAGMA table_info(opportunities)")}
    tables = {row[0] for row in reopened.db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {row[0] for row in reopened.db.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "routing_diagnostics_json" in columns
    assert "settlement_audits" in tables
    assert {"idx_settlement_audits_opportunity", "idx_settlement_audits_status"}.issubset(indexes)
    assert reopened.db._schema_is_current() is True

    overview = reopened.dashboard_overview({})
    assert overview["ok"] is True
    assert overview["accounts"]["betfair"]["mode"] == "sim"
    assert overview["venue_metrics"]["betfair"]["capital"] is not None


def test_dashboard_financial_core_survives_optional_routing_diagnostic_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    api = API(tmp_path / "diag-fail.sqlite3")

    def broken_diagnostics(*_args, **_kwargs):
        raise sqlite3.OperationalError("simulated optional diagnostic failure")

    monkeypatch.setattr(api.db, "exchange_routing_diagnostics", broken_diagnostics)
    overview = api.dashboard_overview({})
    assert overview["ok"] is True
    assert overview["working_bankroll"] > 0
    assert overview["accounts"]["betfair"]["equity"] > 0
    assert overview["wallet_drift"]["routing_diagnostics_available"] is False
    assert "simulated optional diagnostic failure" in overview["wallet_drift"]["routing_diagnostics_error"]


def test_live_dashboard_clear_and_loader_are_guarded_by_selected_mode():
    assert "A stale LIVE request must never blank the SIM dashboard after a mode switch." in HTML
    assert "if(typeof dataContextMode!=='undefined'&&normalizedMode(dataContextMode)!=='live')return;" in HTML
    assert "if(normalizedMode(dataContextMode||'sim')!=='live')return {ok:false,stale_context:true,mode:'sim'};" in HTML
