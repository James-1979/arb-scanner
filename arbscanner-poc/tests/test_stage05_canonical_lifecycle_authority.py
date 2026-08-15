from __future__ import annotations

import importlib.util
from pathlib import Path

from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]


def load_probe_module():
    path = ROOT / "scripts" / "refactor_probe.py"
    spec = importlib.util.spec_from_file_location("stage05_refactor_probe", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def authority_writes(statements: list[str]) -> list[str]:
    probe = load_probe_module()
    out = []
    for statement in statements:
        if probe.sql_op(statement) not in probe.WRITE_OPS:
            continue
        table = probe.target_table(statement)
        if probe.classify_table(table) == "authority":
            out.append(statement)
    return out


def open_position(api: API, *, corrupt: bool = False) -> int:
    api.db.reset_monitor_wallets({"betfair": 200.0, "matchbook": 200.0})
    oid = api.db.add_opportunity(
        "stage05-event", "A v B", None, "Match Winner", 1.0, 1.0,
        [], [], 1.0, f"stage05-{int(corrupt)}-{id(api)}",
    )
    # Empty fill evidence preserves the historical compatibility path while still
    # exercising wallet, position and result authority in one transaction.
    outcome_pnls = {
        "A": {"betfair": 5.0 if not corrupt else 6.0, "matchbook": -2.0},
        "B": {"betfair": -2.0, "matchbook": 5.0},
    }
    simulation = {}
    if corrupt:
        # Provide fill evidence that deliberately disagrees with the stored net
        # outcome so the Monitor settlement fails closed before any commit.
        simulation = {
            "fills": [
                {"venue_id": "betfair", "selection": "A", "side": "BACK", "stake": 10.0, "odds": 2.0, "commission_pct": 0.0},
                {"venue_id": "matchbook", "selection": "B", "side": "BACK", "stake": 10.0, "odds": 2.0, "commission_pct": 0.0},
            ]
        }
    ok, reason = api.db.open_monitor_position(
        opportunity_id=oid,
        execution_run_id=None,
        event_key="stage05-event",
        market_name="Match Winner",
        deployed=20.0,
        expected_profit=3.0,
        stakes_by_exchange={"betfair": 10.0, "matchbook": 10.0},
        outcome_exchange_pnls=outcome_pnls,
        simulation=simulation,
        hedge_reserve_pct=0.0,
    )
    assert ok, reason
    return oid


def wallet_state(api: API):
    rows = api.db.conn.execute(
        "SELECT stream,exchange,available_balance,reserved_balance,realized_pnl FROM monitor_stream_wallets ORDER BY stream,exchange"
    ).fetchall()
    return [tuple(row) for row in rows]


def test_atomic_canonical_settlement_aligns_monitor_result_and_opportunity(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    api = API(tmp_path / "canonical.sqlite3")
    oid = open_position(api)

    result = api.db.settle_canonical_lifecycle(oid, "A", notes="stage05 canonical")
    assert result["ok"] is True
    assert result["monitor"]["ok"] is True

    monitor = api.db.conn.execute(
        "SELECT status,settled_at,outcome,realized_pnl FROM monitor_positions WHERE opportunity_id=?", (oid,)
    ).fetchone()
    settled = api.db.conn.execute(
        "SELECT settled_at,outcome FROM settlements WHERE opportunity_id=?", (oid,)
    ).fetchone()
    opportunity = api.db.conn.execute("SELECT status FROM opportunities WHERE id=?", (oid,)).fetchone()

    assert monitor["status"] == "SETTLED"
    assert opportunity["status"] == "settled"
    assert monitor["outcome"] == settled["outcome"] == "A"
    assert monitor["settled_at"] == settled["settled_at"] == result["settled_at"]
    assert api.db.lifecycle_authority_integrity(oid)["ok"] is True


def test_canonical_settlement_rolls_back_monitor_phase_if_result_phase_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    api = API(tmp_path / "rollback.sqlite3")
    oid = open_position(api)
    before_wallets = wallet_state(api)

    original_settle = api.db.settle

    def fail_result(*args, **kwargs):
        raise RuntimeError("stage05 injected result failure")

    monkeypatch.setattr(api.db, "settle", fail_result)
    try:
        try:
            api.db.settle_canonical_lifecycle(oid, "A")
            raise AssertionError("expected injected failure")
        except RuntimeError as exc:
            assert "injected result failure" in str(exc)
    finally:
        monkeypatch.setattr(api.db, "settle", original_settle)

    monitor = api.db.conn.execute(
        "SELECT status,settled_at,outcome,realized_pnl FROM monitor_positions WHERE opportunity_id=?", (oid,)
    ).fetchone()
    assert tuple(monitor) == ("OPEN", None, None, None)
    assert wallet_state(api) == before_wallets
    assert api.db.conn.execute("SELECT COUNT(*) FROM settlements WHERE opportunity_id=?", (oid,)).fetchone()[0] == 0
    assert api.db.conn.execute("SELECT status FROM opportunities WHERE id=?", (oid,)).fetchone()[0] != "settled"


def test_reconciliation_failure_rolls_back_complete_canonical_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    api = API(tmp_path / "reconciliation.sqlite3")
    oid = open_position(api, corrupt=True)
    before_wallets = wallet_state(api)

    result = api.db.settle_canonical_lifecycle(oid, "A")
    assert result["ok"] is False
    assert result["reason"] == "settlement_reconciliation_error"
    assert wallet_state(api) == before_wallets
    assert api.db.conn.execute("SELECT status FROM monitor_positions WHERE opportunity_id=?", (oid,)).fetchone()[0] == "OPEN"
    assert api.db.conn.execute("SELECT COUNT(*) FROM settlements WHERE opportunity_id=?", (oid,)).fetchone()[0] == 0


def test_integrity_report_detects_drift_without_repairing_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    api = API(tmp_path / "integrity.sqlite3")
    oid = open_position(api)
    standalone = api.db.settle_monitor_position(oid, "A")
    assert standalone["ok"] is True
    assert api.db.conn.execute("SELECT COUNT(*) FROM settlements WHERE opportunity_id=?", (oid,)).fetchone()[0] == 0

    statements: list[str] = []
    api.db.conn.set_trace_callback(statements.append)
    report = api.db.lifecycle_authority_integrity(oid)
    api.db.conn.set_trace_callback(None)

    assert report["ok"] is False
    assert report["read_only"] is True
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["settled_monitor_missing_result"]["count"] == 1
    assert authority_writes(statements) == []
    assert api.db.conn.execute("SELECT COUNT(*) FROM settlements WHERE opportunity_id=?", (oid,)).fetchone()[0] == 0

    # An explicit lifecycle write may resolve the reported drift; the report itself may not.
    repaired = api.db.settle_canonical_lifecycle(oid, "A", notes="explicit repair boundary")
    assert repaired["ok"] is True
    assert api.db.lifecycle_authority_integrity(oid)["ok"] is True


def test_runtime_scanner_uses_single_canonical_settlement_boundary():
    source = (ROOT / "arbscanner" / "scanner.py").read_text()
    start = source.index("async def settle_once_async")
    end = source.index("    def settle_once(self)", start)
    settle_block = source[start:end]
    assert ".settle_canonical_lifecycle(" in settle_block
    assert ".settle_monitor_position(" not in settle_block
    assert ".settle(" not in settle_block
