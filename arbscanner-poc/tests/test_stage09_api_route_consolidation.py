import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_PATH = ROOT / "arbscanner" / "api.py"
REMOVED = {
    "cancel_job_schedule",
    "settle_now",
    "engine_types",
    "detailed_market_history",
    "archive_reset_history",
    "daily_summary",
}


def _public_api_methods():
    tree = ast.parse(API_PATH.read_text(encoding="utf-8"))
    api_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "API")
    return {
        node.name
        for node in api_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_")
    }


def _consumer_files():
    files = [ROOT / "worker.py", ROOT / "app.py"]
    for base in (ROOT / "frontend", ROOT / "tests", ROOT / "arbscanner"):
        for path in base.rglob("*"):
            if not path.is_file() or path == API_PATH:
                continue
            if path.suffix.lower() in {".py", ".html", ".js"}:
                files.append(path)
    return files


def test_removed_routes_have_no_repository_consumers_and_are_not_public():
    public = _public_api_methods()
    assert not (REMOVED & public)
    for route in sorted(REMOVED):
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(route)}(?![A-Za-z0-9_])")
        hits = []
        for path in _consumer_files():
            if path == Path(__file__).resolve():
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                hits.append(str(path.relative_to(ROOT)))
        assert hits == [], f"removed route {route} still has consumers: {hits}"


def test_every_literal_frontend_rpc_target_resolves_to_public_api():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    routes = set(re.findall(r"window\.pywebview\.api\.([A-Za-z_]\w*)", html))
    routes.update(re.findall(r"\bcall\(\s*['\"]([A-Za-z_]\w*)['\"]", html))
    missing = sorted(routes - _public_api_methods())
    assert missing == []
    assert len(routes) >= 50


def test_operation_boundary_classifies_every_public_api_method_exactly_once():
    payload = json.loads((ROOT / "validation" / "refactor_operation_boundaries.json").read_text(encoding="utf-8"))
    classified = []
    for category in ("command", "diagnostic", "lifecycle_write", "maintenance", "query"):
        classified.extend(payload["api"][category])
    assert len(classified) == len(set(classified)), "API operation categories overlap"
    public = _public_api_methods()
    # background service convenience methods and the explicit demo scan are classified too;
    # the manifest is intended to cover the entire public pywebview surface.
    assert set(classified) == public
    assert payload["stage"] == 9


def test_route_manifest_matches_removed_and_retained_contracts():
    payload = json.loads((ROOT / "validation" / "refactor_api_routes.json").read_text(encoding="utf-8"))
    assert set(payload["removed_unreferenced_routes"]) == REMOVED
    retained = payload["retained_compatibility_routes"]
    public = _public_api_methods()
    for route in ("activate_monitor_timing", "stop_monitor_timing", "set_feed_enabled", "engine_import_package", "live_view_data"):
        assert route in retained
        assert route in public


def test_removed_routes_are_not_benchmark_projections():
    payload = json.loads((ROOT / "validation" / "refactor_projection_manifest.json").read_text(encoding="utf-8"))
    projected = {item["method"] for item in payload["projections"]}
    assert not (REMOVED & projected)
