from __future__ import annotations

import platform
import subprocess
from typing import Any

BAND_RANK = {"Invalid": 0, "Tiny": 1, "Thin": 2, "Usable": 3, "Strong": 4, "Excellent": 5}


def qualifies_for_alert(profile: dict[str, Any], cfg: dict[str, Any]) -> tuple[bool, str]:
    if not cfg.get("alerts_enabled", True):
        return False, "alerts disabled"
    band = str(profile.get("quality_band") or "Invalid")
    allowed = set(cfg.get("alert_quality_bands") or ["Usable", "Strong", "Excellent"])
    if band not in allowed:
        return False, f"quality {band} not enabled"
    checks = [
        (float(profile.get("deployed_roi_pct") or 0.0) >= float(cfg.get("alert_min_deployed_roi_pct", 0.75)), "deployed ROI"),
        (float(profile.get("bankroll_roi_pct") or 0.0) >= float(cfg.get("alert_min_bankroll_roi_pct", 0.0)), "bankroll ROI"),
        (float(profile.get("capital_used_pct") or 0.0) >= float(cfg.get("alert_min_capital_used_pct", 0.0)), "capital usage"),
        (float(profile.get("expected_profit") or 0.0) >= float(cfg.get("alert_min_profit", 1.0)), "Monitor profit"),
    ]
    failed = [name for ok, name in checks if not ok]
    return (not failed, "passed" if not failed else "below " + ", ".join(failed))


def send_macos_notification_diagnostic(title: str, message: str, sound: bool = True) -> dict[str, Any]:
    """Run the macOS notification command and return delivery diagnostics."""
    if platform.system() != "Darwin":
        return {
            "ok": False,
            "message": "Desktop notifications are only available in the macOS build.",
            "returncode": None,
        }

    def esc(value: str) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{esc(message)}" with title "{esc(title)}"'
    if sound:
        script += ' sound name "Glass"'
    try:
        proc = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            check=False,
            timeout=10,
            capture_output=True,
            text=True,
        )
        detail = (proc.stderr or proc.stdout or "").strip()
        if proc.returncode == 0:
            return {"ok": True, "message": "macOS accepted the notification request.", "returncode": 0}
        return {
            "ok": False,
            "message": detail or f"osascript exited with status {proc.returncode}.",
            "returncode": proc.returncode,
        }
    except Exception as exc:
        return {"ok": False, "message": f"Notification command failed: {exc}", "returncode": None}


def send_macos_notification(title: str, message: str, sound: bool = True) -> bool:
    return bool(send_macos_notification_diagnostic(title, message, sound=sound).get("ok"))
