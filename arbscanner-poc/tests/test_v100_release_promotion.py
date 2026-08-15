from pathlib import Path

from arbscanner import __version__
from arbscanner.engine_packages import PACKAGE_PLATFORM_VERSION


ROOT = Path(__file__).resolve().parents[1]


def test_v100_release_identity_is_consistent():
    assert __version__ == "1.0"
    assert PACKAGE_PLATFORM_VERSION == "1.0"

    frontend = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    installer = (ROOT / "BUILD_AND_INSTALL.command").read_text(encoding="utf-8")
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    notes = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "<title>ArbScanner v1.0</title>" in frontend
    assert 'ArbScanner <span id="version">v1.0</span>' in frontend
    assert "EXPECTED_VERSION=\"1.0\"" in installer
    assert '"ArbScanner",' in app
    assert "## 1.0 — Verified Production Baseline" in notes
    assert readme.startswith("# ArbScanner v1.0\n")


def test_v100_release_does_not_reopen_live_execution():
    api = (ROOT / "arbscanner" / "api.py").read_text(encoding="utf-8")
    # Existing central lock contract must remain present in the promoted source.
    assert '"live_order_writes": False' in api
    assert '"live_execution_allowed": False' in api
    assert 'LIVE is intentionally locked: this build contains no real venue order-placement path.' in api
