from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.models import Leg
from arbscanner.strategy_engines import EngineRuntime, MarketEvidence

ROOT = Path(__file__).resolve().parents[1]


def _evidence(one_venue: bool, snapshot: str = "snap") -> MarketEvidence:
    def leg(exchange, provider, selection, odds, market):
        return Leg(exchange, selection, odds, 100.0, 2.0, market_id=market,
                   selection_id=f"{market}-{selection}", provider_id=provider, venue_id=provider)
    candidates = {
        "A": [leg("Betfair delayed", "betfair", "A", 2.2, "bf-a")],
        "B": [leg("Betfair delayed", "betfair", "B", 2.2, "bf-b")],
    }
    if not one_venue:
        candidates["A"].append(leg("Matchbook", "matchbook", "A", 2.15, "mb-a"))
        candidates["B"].append(leg("Matchbook", "matchbook", "B", 2.15, "mb-b"))
    market = SimpleNamespace(
        canonical_event_id=snapshot, event_key=snapshot, canonical_market_id=snapshot,
        display_market="Match Winner", display_event="A v B", start_time="2026-08-14T12:00:00+00:00",
        section="sports", sport="Tennis", competition="Test", strategy="two-way", status="OPEN",
        in_play=False, canonical_market_type="Match Winner",
    )
    return MarketEvidence.from_candidates(market, candidates, feed_generation="g1", observed_at="2026-08-14T09:00:00+00:00")


def _package(manifest: dict, source: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("engine.py", source)
    return buf.getvalue()


def _manifest(engine_type="SAFE_TEST_ENGINE", iid="SAFE_TEST_ENGINE_PRIMARY", version="1.0.0"):
    return {
        "format_version": 1,
        "engine_api_version": 1,
        "engine_type": engine_type,
        "engine_instance_id": iid,
        "display_name": "Safe Test Engine",
        "engine_version": version,
        "engine_grade": "RESEARCH",
        "implementation_kind": "restricted_python",
        "engine_class": "Engine",
        "capabilities": ["SPORTS", "PRE_MATCH"],
        "config_schema": {},
        "default_config": {},
        "section": "sports",
    }


def _safe_source():
    return '''class Engine(StrategyEngine):
    engine_type = "SAFE_TEST_ENGINE"
    display_name = "Safe Test Engine"
    engine_version = "1.0.0"
    engine_grade = "RESEARCH"
    capabilities = ("SPORTS", "PRE_MATCH")
    config_schema = {}
    def evaluate(self, context):
        return EngineEvaluation(context, (), (), None, 0.0)
'''


def test_0938_bad_market_never_globally_disables_enabled_engine_and_recovers(tmp_path):
    db = DB(tmp_path / "scanner.sqlite3")
    db.ensure_default_engines()
    iid = "SPORTS_BASELINE_ARB_PRIMARY"
    rt = EngineRuntime(db)
    local = rt.evaluate(_evidence(True, "bad"), instance_ids=[iid])
    assert len(local) == 1 and local[0].decision is None
    after_bad = db.engine_instance(iid)
    assert after_bad["sim_enabled"] is True
    assert after_bad["effective_lifecycle"] == "SIM"
    assert after_bad["effective_reason"] == "SIM_ENABLED"
    # Heal the exact stale global state persisted by the 0.9.36 regression.
    db.engine_set_effective(iid, "DISABLED", "INSUFFICIENT_COMPATIBLE_VENUE_FEEDS")
    valid = rt.evaluate(_evidence(False, "good"), instance_ids=[iid])
    assert valid and valid[0].decision is not None
    healed = db.engine_instance(iid)
    assert healed["sim_enabled"] is True
    assert healed["effective_lifecycle"] == "SIM"
    assert healed["effective_reason"] == "SIM_ENABLED"


def test_0938_last_detected_is_first_detection_not_refresh_or_repeat_scan(tmp_path):
    db = DB(tmp_path / "last-detected.sqlite3")
    db.ensure_default_engines()
    iid = "SPORTS_BASELINE_ARB_PRIMARY"
    rows = [
        (iid, "same-snapshot", "2026-08-14T09:00:02+00:00", "2026-08-14T09:00:00+00:00", "sim", "sports", "Tennis", "A v B", "Match Winner", "Match Winner", "pre_match", None, 0, '["betfair","matchbook"]'),
        (iid, "same-market-new-poll", "2026-08-14T09:01:02+00:00", "2026-08-14T09:01:00+00:00", "sim", "sports", "Tennis", "A v B", "Match Winner", "Match Winner", "pre_match", None, 0, '["betfair","matchbook"]'),
    ]
    db.conn.executemany(
        """INSERT INTO engine_evaluations(engine_instance_id,market_snapshot_id,evaluated_at,observed_at,mode,section,sport,event_name,market_name,market_type,stream,decision_id,had_opportunity,venue_ids_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows,
    )
    db.conn.commit()
    first = db.monitor_last_detected(mode="sim", section="sports", engine=iid, sport="Tennis")
    assert first["detected_at"] == "2026-08-14T09:00:00+00:00"
    # A new snapshot is a genuinely new detection and advances the timestamp.
    db.conn.execute(
        """INSERT INTO engine_evaluations(engine_instance_id,market_snapshot_id,evaluated_at,observed_at,mode,section,sport,event_name,market_name,market_type,stream,decision_id,had_opportunity,venue_ids_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (iid, "new-snapshot", "2026-08-14T09:02:02+00:00", "2026-08-14T09:02:00+00:00", "sim", "sports", "Tennis", "C v D", "Match Winner", "Match Winner", "pre_match", None, 0, '["betfair","matchbook"]'),
    )
    db.conn.commit()
    second = db.monitor_last_detected(mode="sim", section="sports", engine=iid, sport="Tennis")
    assert second["detected_at"] == "2026-08-14T09:02:00+00:00"
    assert db.monitor_last_detected(mode="live", section="sports", engine=iid)["detected_at"] is None


def test_0938_engine_upload_validation_is_quarantine_only_and_install_is_explicit(tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir(); monkeypatch.setenv("HOME", str(home))
    api = API(tmp_path / "api.sqlite3")
    raw = _package(_manifest(), _safe_source())
    reviewed = api.engine_validate_package({"filename": "safe.arbengine", "package_base64": base64.b64encode(raw).decode()})
    assert reviewed["ok"] is True
    assert reviewed["install_kind"] == "NEW_INSTALL"
    assert reviewed["requires_confirmation"] is True
    assert reviewed["code_executed"] is False
    assert api.db.engine_instance("SAFE_TEST_ENGINE_PRIMARY") is None
    refused = api.engine_install_quarantined_package({"quarantine_token": reviewed["quarantine_token"], "confirm": False})
    assert refused["ok"] is False
    installed = api.engine_install_quarantined_package({"quarantine_token": reviewed["quarantine_token"], "confirm": True})
    assert installed["ok"] is True
    row = api.db.engine_instance("SAFE_TEST_ENGINE_PRIMARY")
    assert row is not None
    assert row["sim_enabled"] is False and row["live_enabled"] is False
    assert row["effective_lifecycle"] == "DISABLED"
    assert row["package_sha256"] == reviewed["package_sha256"]
    assert row["package_filename"].endswith(".arbengine")


def test_0938_uploaded_code_does_not_execute_during_static_validation(tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir(); monkeypatch.setenv("HOME", str(home))
    api = API(tmp_path / "api.sqlite3")
    m = _manifest("BOOM_TEST_ENGINE", "BOOM_TEST_ENGINE_PRIMARY")
    src = '''raise Exception("UPLOAD_CODE_EXECUTED")
class Engine(StrategyEngine):
    engine_type="BOOM_TEST_ENGINE"
    display_name="Boom"
    engine_version="1.0.0"
    engine_grade="RESEARCH"
    capabilities=()
    config_schema={}
    def evaluate(self, context):
        return EngineEvaluation(context, (), (), None, 0.0)
'''
    raw = _package(m, src)
    reviewed = api.engine_validate_package({"filename": "boom.arbengine", "package_base64": base64.b64encode(raw).decode()})
    assert reviewed["ok"] is True and reviewed["code_executed"] is False
    # Execution is deferred until explicit installation, where the package fails safely.
    installed = api.engine_install_quarantined_package({"quarantine_token": reviewed["quarantine_token"], "confirm": True})
    assert installed["ok"] is False
    assert "UPLOAD_CODE_EXECUTED" in installed["message"]
    assert api.db.engine_instance("BOOM_TEST_ENGINE_PRIMARY") is None


def test_0938_monitor_engines_and_refresh_ui_contract():
    html = (ROOT / "frontend" / "index.html").read_text()
    assert html.count('id="monitorPhase"') == 1
    assert html.count('id="monitorEngine0917"') == 1
    assert html.count('id="monitorStatus"') == 1
    assert html.count('id="monitorSport"') == 1
    assert html.count('id="monitorMarket0936"') == 1
    assert html.count('id="monitorVenue0917"') == 1
    assert html.count('id="monitorAccount0917"') == 1
    assert html.count('id="monitorSearch"') == 1
    assert 'id="monitorLastDetected0938"' in html
    assert 'monitorLastDetectedEpoch0938' in html and 'epoch!==monitorLastDetectedEpoch0938' in html
    assert '>Engine provenance<' not in html
    assert 'ROUTING / LEGACY CONTEXT' not in html
    assert "src==='routing_only'?'Routed':'Unverified engine'" in html
    assert '<h3 style="margin:0">Engines</h3>' in html
    assert '<th>Last Activity</th><th>Enabled</th>' in html
    assert 'openEngineDrawer0938' in html
    assert '+ Add Engine' in html
    assert 'Upload → Execute' not in html
    assert 'engine_validate_package' in html and 'engine_install_quarantined_package' in html
    assert 'Static validation only' in (ROOT / "arbscanner" / "api.py").read_text()
    assert '◌ Refreshing…' in html and '✓ Updated' in html and 'Refresh failed' in html


def test_0938_engine_upload_rejects_archive_path_escape_before_install(tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir(); monkeypatch.setenv("HOME", str(home))
    api = API(tmp_path / "unsafe.sqlite3")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(_manifest("UNSAFE_TEST_ENGINE", "UNSAFE_TEST_ENGINE_PRIMARY")))
        zf.writestr("../escape.py", "raise RuntimeError('must never be installed')\n")
        zf.writestr("engine.py", _safe_source().replace("SAFE_TEST_ENGINE", "UNSAFE_TEST_ENGINE"))
    reviewed = api.engine_validate_package({
        "filename": "unsafe.arbengine",
        "package_base64": base64.b64encode(buf.getvalue()).decode(),
    })
    assert reviewed["ok"] is False
    assert "path" in reviewed["message"].lower() or "travers" in reviewed["message"].lower()
    assert api.db.engine_instance("UNSAFE_TEST_ENGINE_PRIMARY") is None


def test_0938_failed_upgrade_review_never_replaces_installed_package(tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir(); monkeypatch.setenv("HOME", str(home))
    api = API(tmp_path / "upgrade.sqlite3")
    manifest = _manifest("UPGRADE_SAFE_ENGINE", "UPGRADE_SAFE_ENGINE_PRIMARY", "1.0.0")
    source = _safe_source().replace("SAFE_TEST_ENGINE", "UPGRADE_SAFE_ENGINE")
    first_raw = _package(manifest, source)
    first_review = api.engine_validate_package({"filename": "first.arbengine", "package_base64": base64.b64encode(first_raw).decode()})
    first = api.engine_install_quarantined_package({"quarantine_token": first_review["quarantine_token"], "confirm": True})
    assert first["ok"] is True
    before = api.db.engine_instance("UPGRADE_SAFE_ENGINE_PRIMARY")
    package_path = Path(before["package_source"])
    original = package_path.read_bytes()
    original_hash = before["package_sha256"]

    bad_source = source.replace("class Engine(StrategyEngine):", 'raise Exception("BAD_UPGRADE_EXECUTED")\nclass Engine(StrategyEngine):')
    second_raw = _package(manifest, bad_source)
    second_review = api.engine_validate_package({"filename": "bad-upgrade.arbengine", "package_base64": base64.b64encode(second_raw).decode()})
    assert second_review["ok"] is True and second_review["install_kind"] == "UPGRADE_REVIEW"
    failed = api.engine_install_quarantined_package({"quarantine_token": second_review["quarantine_token"], "confirm": True})
    assert failed["ok"] is False and "BAD_UPGRADE_EXECUTED" in failed["message"]
    after = api.db.engine_instance("UPGRADE_SAFE_ENGINE_PRIMARY")
    assert after["package_sha256"] == original_hash
    assert package_path.read_bytes() == original
