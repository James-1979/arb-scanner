from pathlib import Path
import sys
import webview
from arbscanner.api import API


def resource_path(*parts: str) -> Path:
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS).joinpath(*parts)
    return Path(__file__).resolve().parent.joinpath(*parts)


if __name__ == "__main__":
    api = API()
    index = resource_path("frontend", "index.html")
    webview.create_window(
        "ArbScanner",
        index.as_uri(),
        js_api=api,
        width=1280,
        height=860,
        min_size=(980, 680),
    )
    webview.start(debug=False)
