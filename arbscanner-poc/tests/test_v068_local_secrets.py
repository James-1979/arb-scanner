import json
import os
import sys
from pathlib import Path

from arbscanner.secrets import BUNDLE_KEY, SERVICE, SecretStore


class FakeKeyring:
    def __init__(self):
        self.data = {}
        self.get_calls = []

    def get_password(self, service, key):
        self.get_calls.append((service, key))
        return self.data.get((service, key))


def test_local_json_round_trip_and_documented_shape(tmp_path: Path):
    path = tmp_path / "ArbScanner" / "secrets.json"
    store = SecretStore(secrets_path=path)
    store.set_many({
        "matchbook_username": "james@example.test",
        "matchbook_password": "pw",
        "matchbook_session_token": "mb-session",
        "betfair_app_key": "bf-key",
        "betfair_session_token": "bf-session",
    })

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["matchbook"]["username"] == "james@example.test"
    assert raw["matchbook"]["password"] == "pw"
    assert raw["matchbook"]["session_token"] == "mb-session"
    assert raw["betfair"]["delayed_app_key"] == "bf-key"
    assert raw["betfair"]["session_token"] == "bf-session"

    reopened = SecretStore(secrets_path=path)
    assert reopened.get("matchbook_password") == "pw"
    assert reopened.get("betfair_app_key") == "bf-key"
    assert reopened.presence()["betfair_session_token"] is True


def test_blank_bulk_updates_do_not_erase_existing_values(tmp_path: Path):
    path = tmp_path / "secrets.json"
    store = SecretStore(secrets_path=path)
    store.set_many({"matchbook_password": "keep-me", "betfair_app_key": "keep-key"})
    store.set_many({"matchbook_password": "", "betfair_app_key": "   "})
    assert store.get("matchbook_password") == "keep-me"
    assert store.get("betfair_app_key") == "keep-key"


def test_explicit_clear_removes_only_requested_value(tmp_path: Path):
    path = tmp_path / "secrets.json"
    store = SecretStore(secrets_path=path)
    store.set_many({"matchbook_password": "pw", "betfair_app_key": "key"})
    store.set("matchbook_password", "")
    assert store.get("matchbook_password") is None
    assert store.get("betfair_app_key") == "key"


def test_posix_permissions_are_private(tmp_path: Path):
    path = tmp_path / "ArbScanner" / "secrets.json"
    store = SecretStore(secrets_path=path)
    store.ensure_file()
    if os.name != "nt":
        assert (path.parent.stat().st_mode & 0o777) == 0o700
        assert (path.stat().st_mode & 0o777) == 0o600


def test_normal_reads_do_not_touch_keychain(monkeypatch, tmp_path: Path):
    fake = FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", fake)
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({
        "version": 1,
        "matchbook": {"username": "u", "password": "pw", "session_token": "s"},
        "betfair": {"delayed_app_key": "k", "session_token": "t"},
    }), encoding="utf-8")
    store = SecretStore(secrets_path=path)
    assert store.get("matchbook_password") == "pw"
    assert store.get("betfair_session_token") == "t"
    assert store.presence()["betfair_app_key"] is True
    assert fake.get_calls == []


def test_optional_v067_keychain_import_writes_local_file(monkeypatch, tmp_path: Path):
    fake = FakeKeyring()
    fake.data[(SERVICE, BUNDLE_KEY)] = json.dumps({
        "matchbook_password": "old-pw",
        "matchbook_session_token": "old-mb-session",
        "betfair_app_key": "old-key",
        "betfair_session_token": "old-bf-session",
    })
    monkeypatch.setitem(sys.modules, "keyring", fake)
    path = tmp_path / "secrets.json"
    store = SecretStore(secrets_path=path)
    result = store.import_legacy_keychain()
    assert result["ok"] is True
    assert store.get("matchbook_password") == "old-pw"
    assert store.get("betfair_app_key") == "old-key"
    assert fake.get_calls[0] == (SERVICE, BUNDLE_KEY)
