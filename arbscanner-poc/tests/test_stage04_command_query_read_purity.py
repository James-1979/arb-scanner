from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

from arbscanner.api import API, DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parents[1]
BOUNDARIES = ROOT / "validation" / "refactor_operation_boundaries.json"


def load_probe_module():
    path = ROOT / "scripts" / "refactor_probe.py"
    spec = importlib.util.spec_from_file_location("stage04_refactor_probe", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def public_api_methods() -> set[str]:
    tree = ast.parse((ROOT / "arbscanner" / "api.py").read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "API")
    return {
        node.name
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_")
    }


def method_source(name: str) -> str:
    tree = ast.parse((ROOT / "arbscanner" / "api.py").read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "API")
    lines = (ROOT / "arbscanner" / "api.py").read_text().splitlines()
    node = next(node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)
    return "\n".join(lines[node.lineno - 1 : node.end_lineno])


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


def delete_wallet_authority(api: API) -> None:
    with api.db.lock:
        api.db.conn.execute("DELETE FROM monitor_stream_wallets WHERE stream='racing' AND exchange='betfair'")
        api.db.conn.execute("DELETE FROM monitor_wallets WHERE exchange='betfair'")
        api.db.conn.commit()


def wallet_rows(api: API) -> tuple[int, int]:
    with api.db.lock:
        stream = int(api.db.conn.execute(
            "SELECT COUNT(*) FROM monitor_stream_wallets WHERE stream='racing' AND exchange='betfair'"
        ).fetchone()[0])
        legacy = int(api.db.conn.execute(
            "SELECT COUNT(*) FROM monitor_wallets WHERE exchange='betfair'"
        ).fetchone()[0])
    return stream, legacy


def test_every_public_api_method_has_exactly_one_stage04_boundary_classification():
    manifest = json.loads(BOUNDARIES.read_text())
    assigned = [name for names in manifest["api"].values() for name in names]
    assert len(assigned) == len(set(assigned)), "a public API method is classified more than once"
    assert set(assigned) == public_api_methods()


def test_query_methods_do_not_directly_invoke_wallet_authority_repair():
    manifest = json.loads(BOUNDARIES.read_text())
    offenders = []
    for name in manifest["api"]["query"]:
        source = method_source(name)
        if ".ensure_monitor_streams(" in source or ".ensure_monitor_wallets(" in source:
            offenders.append(name)
    assert offenders == []


def test_wallet_tables_are_classified_as_authority_not_derived_or_other():
    probe = load_probe_module()
    for table in ("monitor_wallets", "monitor_stream_wallets", "sim_account_adjustments"):
        assert probe.classify_table(table) == "authority"
    assert probe.classify_table("live_account_snapshots") == "audit"


def test_ui_sim_queries_report_wallet_drift_without_repairing_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cases = (
        ("scenario_capital_sources", {}),
        ("sim_portfolio_budget_overview", {}),
        ("dashboard_overview", {"mode": "sim"}),
    )
    for index, (method, data) in enumerate(cases):
        api = API(tmp_path / f"query-{index}.sqlite3")
        delete_wallet_authority(api)
        assert wallet_rows(api) == (0, 0)
        statements: list[str] = []
        api.db.conn.set_trace_callback(statements.append)
        result = getattr(api, method)(data)
        api.db.conn.set_trace_callback(None)
        assert isinstance(result, dict)
        assert authority_writes(statements) == [], method
        assert wallet_rows(api) == (0, 0), method
        api.db.conn.close()


def test_private_sim_account_projection_is_read_only_unless_capture_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    api = API(tmp_path / "monitor-state.sqlite3")
    delete_wallet_authority(api)
    cfg = {**DEFAULT_CONFIG, **(api.db.get_setting("config", DEFAULT_CONFIG) or {})}
    statements: list[str] = []
    api.db.conn.set_trace_callback(statements.append)
    result = api._monitor_account_state(cfg, capture=False, context="stage04_test")
    api.db.conn.set_trace_callback(None)
    assert "accounts" in result
    assert authority_writes(statements) == []
    assert wallet_rows(api) == (0, 0)
    api.db.conn.close()


def test_explicit_sim_account_command_can_repair_missing_wallet_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    api = API(tmp_path / "command.sqlite3")
    delete_wallet_authority(api)
    statements: list[str] = []
    api.db.conn.set_trace_callback(statements.append)
    result = api.sim_account_adjust({"exchange": "betfair", "action": "add", "value": 1.0, "reason": "stage04 boundary test"})
    api.db.conn.set_trace_callback(None)
    assert result.get("ok") is True
    assert wallet_rows(api) == (1, 1)
    assert authority_writes(statements), "explicit command should be permitted to write authority"
    api.db.conn.close()
