from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_release_version_and_wide_screen_clock_size():
    assert "PoC 0.9.36" in HTML
    assert "@media (min-width:1321px)" in HTML
    assert ".clock-face{width:84px;height:84px;flex-basis:84px" in HTML
    assert ".analog-clock{gap:10px;min-width:165px}" in HTML


def test_clock_change_is_visual_only_marker_present():
    assert "aspect-ratio:1/1!important" in HTML
