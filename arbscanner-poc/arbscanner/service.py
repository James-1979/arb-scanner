from __future__ import annotations
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.local.arbscanner.worker"


class LaunchAgentManager:
    def __init__(self):
        self.plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    def worker_path(self) -> Path | None:
        exe = Path(sys.executable).resolve()
        # Packaged app: .../ArbScanner.app/Contents/MacOS/ArbScanner
        if ".app" in str(exe):
            contents = exe.parent.parent
            candidate = contents / "Resources" / "ArbScannerWorker"
            if candidate.exists():
                return candidate
        env = os.getenv("ARBSCANNER_WORKER_PATH")
        return Path(env).expanduser() if env else None

    def status(self) -> dict:
        installed = self.plist.exists()
        loaded = False
        if installed and sys.platform == "darwin":
            try:
                uid = os.getuid()
                p = subprocess.run(["launchctl", "print", f"gui/{uid}/{LABEL}"], capture_output=True, text=True)
                loaded = p.returncode == 0
            except Exception:
                loaded = False
        return {"installed": installed, "loaded": loaded, "worker_path": str(self.worker_path() or "")}

    def install(self) -> dict:
        worker = self.worker_path()
        if not worker or not worker.exists():
            return {"ok": False, "message": "Background worker is available after building the macOS app."}
        self.plist.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": LABEL,
            "ProgramArguments": [str(worker)],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Background",
            "StandardOutPath": str(Path.home() / "Library" / "Logs" / "ArbScannerWorker.log"),
            "StandardErrorPath": str(Path.home() / "Library" / "Logs" / "ArbScannerWorker.err.log"),
        }
        with self.plist.open("wb") as f:
            plistlib.dump(payload, f)
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(self.plist)], capture_output=True)
        p = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(self.plist)], capture_output=True, text=True)
        if p.returncode != 0:
            return {"ok": False, "message": p.stderr.strip() or "launchctl bootstrap failed"}
        return {"ok": True, "message": "Background scanner installed", **self.status()}

    def pause(self) -> dict:
        """Stop the loaded worker without removing its LaunchAgent plist."""
        if sys.platform != "darwin" or not self.plist.exists():
            return {"ok": True, "was_loaded": False, "message": "Worker pause not required", **self.status()}
        before = self.status()
        if before.get("loaded"):
            uid = os.getuid()
            subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(self.plist)], capture_output=True)
        return {"ok": True, "was_loaded": bool(before.get("loaded")), "message": "Background scanner paused", **self.status()}

    def resume(self) -> dict:
        """Load an existing LaunchAgent plist after maintenance."""
        if sys.platform != "darwin" or not self.plist.exists():
            return {"ok": True, "message": "Worker resume not required", **self.status()}
        if self.status().get("loaded"):
            return {"ok": True, "message": "Background scanner already running", **self.status()}
        uid = os.getuid()
        p = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(self.plist)], capture_output=True, text=True)
        if p.returncode != 0:
            return {"ok": False, "message": p.stderr.strip() or "launchctl bootstrap failed", **self.status()}
        return {"ok": True, "message": "Background scanner resumed", **self.status()}

    def uninstall(self) -> dict:
        if self.plist.exists():
            uid = os.getuid()
            subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(self.plist)], capture_output=True)
            self.plist.unlink(missing_ok=True)
        return {"ok": True, "message": "Background scanner removed", **self.status()}
