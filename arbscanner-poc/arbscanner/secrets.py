from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

SERVICE = "ArbScannerPoC"
BUNDLE_KEY = "credentials_bundle_v067"
SECRET_KEYS = (
    "matchbook_username",
    "matchbook_password",
    "matchbook_session_token",
    "betfair_app_key",
    "betfair_live_app_key",
    "betfair_session_token",
)


def _default_secrets_path() -> Path:
    if os.name == "nt":
        base = Path.home() / "AppData" / "Local" / "ArbScanner"
    else:
        base = Path.home() / "Library" / "Application Support" / "ArbScanner"
    return base / "secrets.json"


class SecretStore:
    """Persistent local-file credential storage for the personal PoC.

    v0.6.8 deliberately stops using Keychain during normal app and worker
    operation. Exchange credentials are stored in one JSON file under the
    user's ArbScanner Application Support directory. The directory is forced
    to mode 0700 and the file to 0600 on POSIX/macOS.

    This removes Keychain approval prompts across unsigned/ad-hoc rebuilds.
    It is intentionally a personal-PoC trade-off: the file is *not encrypted*
    at rest, so anyone who can read files as the macOS user can read it.
    """

    def __init__(self, secrets_path: Path | None = None):
        self.secrets_path = secrets_path or _default_secrets_path()
        self._memory: dict[str, str] = {}
        self._last_error: str | None = None
        self._ensure_parent()

    def _ensure_parent(self) -> None:
        try:
            self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(self.secrets_path.parent, 0o700)
        except Exception as exc:
            self._last_error = str(exc)

    @staticmethod
    def _blank_document() -> dict:
        return {
            "version": 1,
            "providers": {},
            "matchbook": {
                "username": "",
                "password": "",
                "session_token": "",
            },
            "betfair": {
                "delayed_app_key": "",
                "live_app_key": "",
                "session_token": "",
            },
        }

    @staticmethod
    def _flatten(raw: dict) -> dict[str, str]:
        """Read the documented nested format, while accepting old/flat aliases."""
        out: dict[str, str] = {}
        mb = raw.get("matchbook") if isinstance(raw.get("matchbook"), dict) else {}
        bf = raw.get("betfair") if isinstance(raw.get("betfair"), dict) else {}

        aliases = {
            "matchbook_username": mb.get("username") or raw.get("matchbook_username"),
            "matchbook_password": mb.get("password") or raw.get("matchbook_password"),
            "matchbook_session_token": mb.get("session_token") or raw.get("matchbook_session_token"),
            "betfair_app_key": (
                bf.get("delayed_app_key")
                or bf.get("app_key")
                or raw.get("betfair_app_key")
                or raw.get("betfair_delayed_app_key")
            ),
            "betfair_live_app_key": (
                bf.get("live_app_key")
                or raw.get("betfair_live_app_key")
            ),
            "betfair_session_token": bf.get("session_token") or raw.get("betfair_session_token"),
        }
        for key, value in aliases.items():
            if value is not None and str(value) != "":
                out[key] = str(value)
        return out

    @classmethod
    def _nested(cls, flat: dict[str, str]) -> dict:
        doc = cls._blank_document()
        doc["matchbook"]["username"] = flat.get("matchbook_username", "")
        doc["matchbook"]["password"] = flat.get("matchbook_password", "")
        doc["matchbook"]["session_token"] = flat.get("matchbook_session_token", "")
        doc["betfair"]["delayed_app_key"] = flat.get("betfair_app_key", "")
        doc["betfair"]["live_app_key"] = flat.get("betfair_live_app_key", "")
        doc["betfair"]["session_token"] = flat.get("betfair_session_token", "")
        return doc

    def _load(self) -> dict[str, str]:
        try:
            if not self.secrets_path.exists():
                self._last_error = None
                return dict(self._memory)
            raw = json.loads(self.secrets_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("secrets.json must contain a JSON object")
            flat = self._flatten(raw)
            self._memory = dict(flat)
            self._last_error = None
            return flat
        except Exception as exc:
            self._last_error = str(exc)
            return dict(self._memory)

    def _save(self, flat: dict[str, str]) -> None:
        cleaned = {str(k): str(v) for k, v in flat.items() if v is not None and str(v) != ""}
        self._memory = dict(cleaned)
        try:
            self._ensure_parent()
            tmp = self.secrets_path.with_name(self.secrets_path.name + ".tmp")
            tmp.write_text(json.dumps(self._nested(cleaned), indent=2) + "\n", encoding="utf-8")
            if os.name != "nt":
                os.chmod(tmp, 0o600)
            os.replace(tmp, self.secrets_path)
            if os.name != "nt":
                os.chmod(self.secrets_path, 0o600)
                os.chmod(self.secrets_path.parent, 0o700)
            self._last_error = None
        except Exception as exc:
            self._last_error = str(exc)
            raise RuntimeError(f"Could not save ArbScanner credentials to {self.secrets_path}: {exc}") from exc


    def provider_credentials(self, provider_id: str, profile: str = "default") -> dict[str, str]:
        """Return provider-scoped persistent secrets without exposing runtime sessions.

        0.9.0 keeps the legacy Betfair/Matchbook keys for compatibility while
        introducing a generic profile namespace for future adapters.
        """
        pid = str(provider_id or "").strip().lower()
        profile = str(profile or "default").strip().lower()
        try:
            raw = json.loads(self.secrets_path.read_text(encoding="utf-8")) if self.secrets_path.exists() else {}
        except Exception:
            raw = {}
        providers = raw.get("providers") if isinstance(raw.get("providers"), dict) else {}
        pdata = providers.get(pid) if isinstance(providers.get(pid), dict) else {}
        values = pdata.get(profile) if isinstance(pdata.get(profile), dict) else {}
        return {str(k): str(v) for k, v in values.items() if v is not None and str(v) != ""}

    def set_provider_credentials(self, provider_id: str, profile: str, values: dict[str, str | None]) -> None:
        """Atomically persist a provider/profile secret map for future adapters."""
        pid = str(provider_id or "").strip().lower()
        prof = str(profile or "default").strip().lower()
        if not pid or not prof:
            raise ValueError("provider_id and profile are required")
        # Preserve the documented legacy projections while adding the v2 profile map.
        flat = self._load()
        doc = self._nested(flat)
        try:
            if self.secrets_path.exists():
                existing = json.loads(self.secrets_path.read_text(encoding="utf-8"))
                if isinstance(existing, dict) and isinstance(existing.get("providers"), dict):
                    doc["providers"] = existing["providers"]
        except Exception:
            pass
        providers = doc.setdefault("providers", {})
        pdata = providers.setdefault(pid, {})
        current = pdata.setdefault(prof, {})
        for key, value in (values or {}).items():
            key = str(key)
            if value is None or str(value) == "":
                current.pop(key, None)
            else:
                current[key] = str(value)
        self._ensure_parent()
        tmp = self.secrets_path.with_name(self.secrets_path.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.secrets_path)
        if os.name != "nt":
            os.chmod(self.secrets_path, 0o600)
        self._memory = self._flatten(doc)

    def ensure_file(self) -> Path:
        """Create an empty documented secrets file if one does not already exist."""
        if not self.secrets_path.exists():
            self._save({})
        else:
            if os.name != "nt":
                try:
                    os.chmod(self.secrets_path.parent, 0o700)
                    os.chmod(self.secrets_path, 0o600)
                except Exception as exc:
                    self._last_error = str(exc)
        return self.secrets_path

    def set(self, key: str, value: str | None) -> None:
        if key not in SECRET_KEYS:
            raise KeyError(f"Unknown secret key: {key}")
        flat = self._load()
        value = value or ""
        if value:
            flat[key] = value
        else:
            flat.pop(key, None)
        self._save(flat)

    def set_many(self, values: dict[str, str | None]) -> None:
        """Atomically update multiple non-empty values in one file write.

        Blank values are ignored by design so an empty password field in the UI
        cannot erase a credential that is already stored. Explicit deletion still
        goes through set(key, "") / clear_secret.
        """
        flat = self._load()
        for key, value in values.items():
            if key not in SECRET_KEYS:
                continue
            if value is not None and str(value).strip() != "":
                flat[key] = str(value).strip()
        self._save(flat)

    def get(self, key: str) -> str | None:
        env_key = "ARBSCANNER_" + key.upper().replace("-", "_")
        if os.getenv(env_key):
            return os.environ[env_key]
        value = self._load().get(key)
        return value or None

    def presence(self) -> dict[str, bool]:
        flat = self._load()
        # Match the API's pre-v0.6.8 secret-status keys; username is shown separately.
        return {key: bool(flat.get(key)) for key in SECRET_KEYS if key != "matchbook_username"}

    def status(self) -> dict[str, object]:
        exists = self.secrets_path.exists()
        mode = None
        if exists and os.name != "nt":
            try:
                mode = oct(self.secrets_path.stat().st_mode & 0o777)
            except Exception:
                pass
        return {
            "type": "local_private_json",
            "label": "Local private JSON file · no Keychain prompts",
            "path": str(self.secrets_path),
            "exists": exists,
            "file_mode": mode,
            "encrypted_at_rest": False,
            "protection": "macOS user-only file permissions (0600; directory 0700)",
            "last_error": self._last_error,
        }

    def import_legacy_keychain(self, keys: Iterable[str] | None = None) -> dict[str, object]:
        """Explicit, optional one-time import from v0.6.7/legacy Keychain items.

        Normal operation never imports or opens Keychain. This function only runs
        when the user presses the migration button. It first tries the v0.6.7
        bundled item and then older per-secret items for anything still missing.
        """
        imported: list[str] = []
        missing: list[str] = []
        errors: list[str] = []
        requested = list(keys or [k for k in SECRET_KEYS if k != "matchbook_username"])
        try:
            import keyring
        except Exception as exc:
            return {"ok": False, "imported": [], "missing": requested, "errors": [str(exc)]}

        current = self._load()
        try:
            raw = keyring.get_password(SERVICE, BUNDLE_KEY)
            parsed = json.loads(raw) if raw else {}
            if isinstance(parsed, dict):
                for key in requested:
                    value = parsed.get(key)
                    if value and not current.get(key):
                        current[key] = str(value)
                        imported.append(key)
        except Exception as exc:
            errors.append(f"v0.6.7 bundle: {exc}")

        for key in requested:
            if current.get(key):
                if key not in imported:
                    imported.append(key)
                continue
            try:
                value = keyring.get_password(SERVICE, key)
            except Exception as exc:
                errors.append(f"{key}: {exc}")
                continue
            if value:
                current[key] = str(value)
                imported.append(key)
            else:
                missing.append(key)

        try:
            self._save(current)
        except Exception as exc:
            errors.append(str(exc))
            return {"ok": False, "imported": imported, "missing": missing, "errors": errors}
        return {"ok": bool(imported), "imported": imported, "missing": missing, "errors": errors}
