from __future__ import annotations

"""Portable reviewed-local strategy engine packages for ArbScanner 1.0.

`.arbengine` files are ZIP containers. Upload is quarantine + static validation
only; uploaded source is not executed until the operator explicitly confirms
installation. New instances are created RESEARCH + DISABLED. Dynamic source is
restricted to the engine contract namespace and is intended for reviewed local
packages (for example engines developed together with the operator), not an
untrusted public plugin marketplace.
"""

import ast
import base64
import hashlib
import io
import json
import math
import os
import stat
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

FORMAT_VERSION = 1
ENGINE_API_VERSION = 1
PACKAGE_PLATFORM_VERSION = "1.0"
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_BYTES = 256 * 1024
MAX_EXTRACTED_BYTES = 4 * 1024 * 1024
MAX_PACKAGE_FILES = 64
PACKAGE_EXT = ".arbengine"


def package_store_root() -> Path:
    if os.name == "nt":
        base = Path.home() / "AppData" / "Local" / "ArbScanner"
    else:
        base = Path.home() / "Library" / "Application Support" / "ArbScanner"
    return base / "engine-packages"


def quarantine_root() -> Path:
    path = package_store_root().parent / "engine-quarantine"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_token(value: str) -> str:
    token = str(value or "").strip()
    if not token or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in token):
        raise ValueError("Invalid quarantine token")
    return token


def downloads_dir() -> Path:
    path = Path.home() / "Downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path



def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for token in str(value or "").strip().split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple((parts + [0, 0, 0])[:3])

def _safe_id(value: str, *, label: str = "engine_type") -> str:
    value = str(value or "").strip().upper()
    if not value or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in value):
        raise ValueError(f"{label} may contain only A-Z, 0-9, underscore and hyphen")
    return value


def _clean_manifest(raw: Mapping[str, Any]) -> dict[str, Any]:
    manifest = dict(raw or {})
    if int(manifest.get("format_version") or 0) != FORMAT_VERSION:
        raise ValueError(f"Unsupported .arbengine format version; expected {FORMAT_VERSION}")
    engine_type = _safe_id(manifest.get("engine_type"), label="engine_type")
    engine_api_version = int(manifest.get("engine_api_version") or ENGINE_API_VERSION)
    if engine_api_version != ENGINE_API_VERSION:
        raise ValueError(f"Unsupported engine API version; expected {ENGINE_API_VERSION}")
    version = str(manifest.get("engine_version") or "").strip()
    if not version or len(version) > 64:
        raise ValueError("engine_version is required")
    display_name = str(manifest.get("display_name") or engine_type).strip()[:120]
    grade = str(manifest.get("engine_grade") or "RESEARCH").upper()
    if grade not in {"RESEARCH", "STANDARD", "ADVANCED", "EXTREME"}:
        raise ValueError("Invalid engine_grade")
    kind = str(manifest.get("implementation_kind") or "builtin").lower()
    if kind not in {"builtin", "restricted_python"}:
        raise ValueError("implementation_kind must be builtin or restricted_python")
    capabilities = sorted({str(x).strip().upper() for x in (manifest.get("capabilities") or []) if str(x).strip()})
    config_schema = manifest.get("config_schema") or {}
    default_config = manifest.get("default_config") or {}
    dependencies = manifest.get("dependencies") or manifest.get("requirements") or []
    if not isinstance(config_schema, dict) or not isinstance(default_config, dict):
        raise ValueError("config_schema and default_config must be objects")
    if dependencies:
        raise ValueError("Uploaded engines may not install arbitrary dependencies")
    platform_min_version = str(manifest.get("platform_min_version") or PACKAGE_PLATFORM_VERSION).strip()
    if _version_tuple(platform_min_version) > _version_tuple(PACKAGE_PLATFORM_VERSION):
        raise ValueError(f"Engine requires ArbScanner {platform_min_version} or newer")
    return {
        "format_version": FORMAT_VERSION,
        "engine_api_version": ENGINE_API_VERSION,
        "engine_type": engine_type,
        "display_name": display_name,
        "engine_version": version,
        "engine_grade": grade,
        "capabilities": capabilities,
        "config_schema": config_schema,
        "default_config": default_config,
        "description": str(manifest.get("description") or "").strip()[:4000],
        "notes": str(manifest.get("notes") or "").strip()[:10000],
        "author": str(manifest.get("author") or "Local / reviewed").strip()[:200],
        "implementation_kind": kind,
        "engine_class": str(manifest.get("engine_class") or "Engine").strip()[:120],
        "platform_min_version": platform_min_version,
        "engine_instance_id": (str(manifest.get("engine_instance_id") or "").strip().upper() or None),
        "section": str(manifest.get("section") or "all").strip().lower() or "all",
        "sport": str(manifest.get("sport") or "all").strip() or "all",
        "competition": str(manifest.get("competition") or "all").strip() or "all",
        "market_type": str(manifest.get("market_type") or "all").strip() or "all",
        "package_policy": "reviewed_local",
        "dependencies": [],
        "entry_point": ("engine.py:" + str(manifest.get("engine_class") or "Engine").strip()[:120]) if kind == "restricted_python" else "builtin",
    }


_BANNED_NAMES = {
    "open", "exec", "eval", "compile", "input", "breakpoint", "__import__",
    "globals", "locals", "vars", "dir", "help", "memoryview",
}
_BANNED_ATTR_ROOTS = {"os", "sys", "subprocess", "socket", "pathlib", "requests", "urllib", "http", "shutil", "importlib"}


def validate_restricted_source(source: str) -> ast.AST:
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("engine.py exceeds the package source limit")
    try:
        tree = ast.parse(source, filename="engine.py")
    except SyntaxError as exc:
        raise ValueError(f"engine.py syntax error: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("engine.py imports are not allowed; use the supplied engine contract namespace")
        if isinstance(node, ast.Attribute) and str(node.attr).startswith("__"):
            raise ValueError("dunder attribute access is not allowed in imported engines")
        if isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            raise ValueError(f"forbidden name in engine source: {node.id}")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in _BANNED_ATTR_ROOTS:
            raise ValueError(f"forbidden module access in engine source: {node.value.id}")
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            raise ValueError("global/nonlocal mutation is not allowed in imported engines")
    return tree


def inspect_package_bytes(raw: bytes) -> dict[str, Any]:
    """Static package validation only. Uploaded engine code is never executed here."""
    if not raw or len(raw) > MAX_PACKAGE_BYTES:
        raise ValueError(".arbengine package is empty or exceeds 2 MB")
    digest = hashlib.sha256(raw).hexdigest()
    native_suffixes = {".so", ".dylib", ".dll", ".pyd", ".exe", ".bin", ".class", ".jar"}
    dependency_files = {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "pipfile", "poetry.lock"}
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
            infos = zf.infolist()
            if len(infos) > MAX_PACKAGE_FILES:
                raise ValueError(f"Package contains too many files; maximum is {MAX_PACKAGE_FILES}")
            total_extracted = 0
            names = set()
            for info in infos:
                raw_name = str(info.filename or "").replace("\\", "/")
                path = PurePosixPath(raw_name)
                if not raw_name or raw_name.startswith("/") or path.is_absolute() or ".." in path.parts or any(":" in part for part in path.parts):
                    raise ValueError("unsafe package path")
                mode = (int(info.external_attr) >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise ValueError("symlinks are not allowed in engine packages")
                total_extracted += int(info.file_size or 0)
                if total_extracted > MAX_EXTRACTED_BYTES:
                    raise ValueError("Package extracted size exceeds 4 MB")
                lower = raw_name.lower()
                if Path(lower).suffix in native_suffixes:
                    raise ValueError(f"native/binary payload is not allowed: {raw_name}")
                if Path(lower).name in dependency_files:
                    raise ValueError("dependency installer files are not allowed in engine packages")
                names.add(raw_name)
            if "manifest.json" not in names:
                raise ValueError("manifest.json is required")
            manifest = _clean_manifest(json.loads(zf.read("manifest.json").decode("utf-8")))
            source = None
            if manifest["implementation_kind"] == "restricted_python":
                if "engine.py" not in names:
                    raise ValueError("restricted_python packages require engine.py")
                source = zf.read("engine.py").decode("utf-8")
                tree = validate_restricted_source(source)
                classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
                if manifest["engine_class"] not in classes:
                    raise ValueError("manifest engine_class is not declared in engine.py")
            return {"manifest": manifest, "source": source, "sha256": digest, "bytes": len(raw),
                    "file_count": len(infos), "extracted_bytes": total_extracted, "validation": "static_only"}
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid .arbengine ZIP container") from exc


def inspect_package_base64(payload: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(str(payload or ""), validate=True)
    except Exception as exc:
        raise ValueError("Invalid base64 engine package") from exc
    return inspect_package_bytes(raw)


def quarantine_package_bytes(raw: bytes, *, filename: str = "uploaded.arbengine") -> dict[str, Any]:
    info = inspect_package_bytes(raw)
    token = f"{info['sha256'][:16]}-{uuid.uuid4().hex[:12]}"
    root = quarantine_root()
    package_path = root / f"{token}{PACKAGE_EXT}"
    meta_path = root / f"{token}.json"
    package_path.write_bytes(raw)
    try:
        os.chmod(package_path, 0o600)
    except OSError:
        pass
    metadata = {
        "token": token, "filename": Path(str(filename or "uploaded.arbengine")).name, "sha256": info["sha256"],
        "bytes": info["bytes"], "file_count": info["file_count"], "extracted_bytes": info["extracted_bytes"],
        "created_at": time.time(), "manifest": info["manifest"],
    }
    meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**info, **metadata, "quarantine_path": str(package_path)}


def read_quarantined_package(token: str) -> tuple[bytes, dict[str, Any]]:
    token = _safe_token(token)
    root = quarantine_root(); package_path = root / f"{token}{PACKAGE_EXT}"; meta_path = root / f"{token}.json"
    if not package_path.exists() or not meta_path.exists():
        raise ValueError("Quarantined engine package was not found or has expired")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    raw = package_path.read_bytes(); info = inspect_package_bytes(raw)
    if str(meta.get("sha256")) != info["sha256"]:
        raise ValueError("Quarantined package checksum changed")
    return raw, {**meta, **info}


def remove_quarantined_package(token: str) -> None:
    token = _safe_token(token); root = quarantine_root()
    for suffix in (PACKAGE_EXT, ".json"):
        try: (root / f"{token}{suffix}").unlink()
        except FileNotFoundError: pass


def install_package_bytes(raw: bytes) -> dict[str, Any]:
    info = inspect_package_bytes(raw)
    manifest = info["manifest"]
    root = package_store_root() / manifest["engine_type"] / manifest["engine_version"]
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{manifest['engine_type']}-{manifest['engine_version']}{PACKAGE_EXT}"
    target.write_bytes(raw)
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if info.get("source") is not None:
        (root / "engine.py").write_text(str(info["source"]), encoding="utf-8")
    return {**info, "path": str(target)}


def installed_manifests() -> list[dict[str, Any]]:
    root = package_store_root()
    if not root.exists():
        return []
    out = []
    for path in sorted(root.glob("*/*/*.arbengine")):
        try:
            info = inspect_package_bytes(path.read_bytes())
            out.append({**info["manifest"], "package_path": str(path), "package_sha256": info["sha256"]})
        except Exception:
            continue
    return out


def load_reviewed_engine_class(raw: bytes) -> tuple[type, dict[str, Any]]:
    """Execute a statically reviewed restricted engine package after explicit install confirmation.

    This helper is intentionally *not* used during upload/quarantine validation.  It is
    the runtime-validation boundary used only after the operator confirms installation.
    """
    from .strategy_engines import (
        StrategyEngine, EngineEvaluation, DecisionIntent, DecisionLeg, MarketEvidence,
        stable_hash, utc_now,
    )
    from .engine import best_strategy_legs, diagnose_equal_return, simulate_equal_return, arb_edge

    info = inspect_package_bytes(raw)
    manifest = info["manifest"]
    if manifest["implementation_kind"] != "restricted_python":
        raise ValueError("Only restricted_python engine packages can be loaded by this build")
    source = str(info.get("source") or "")
    validate_restricted_source(source)
    safe_builtins = {
        "__build_class__": __build_class__, "object": object, "super": super,
        "bool": bool, "int": int, "float": float, "str": str, "dict": dict, "list": list,
        "tuple": tuple, "set": set, "frozenset": frozenset, "len": len, "min": min, "max": max,
        "sum": sum, "abs": abs, "round": round, "range": range, "enumerate": enumerate, "zip": zip,
        "sorted": sorted, "any": any, "all": all, "isinstance": isinstance, "Exception": Exception,
        "ValueError": ValueError, "TypeError": TypeError, "KeyError": KeyError,
    }
    namespace = {
        "__builtins__": safe_builtins,
        "__name__": f"arbengine_{manifest['engine_type'].lower()}",
        "StrategyEngine": StrategyEngine, "EngineEvaluation": EngineEvaluation,
        "DecisionIntent": DecisionIntent, "DecisionLeg": DecisionLeg, "MarketEvidence": MarketEvidence,
        "stable_hash": stable_hash, "utc_now": utc_now, "math": math,
        "best_strategy_legs": best_strategy_legs, "diagnose_equal_return": diagnose_equal_return,
        "simulate_equal_return": simulate_equal_return, "arb_edge": arb_edge,
    }
    exec(compile(source, "engine.py", "exec"), namespace, namespace)
    cls = namespace.get(manifest["engine_class"])
    if not isinstance(cls, type) or not issubclass(cls, StrategyEngine) or cls is StrategyEngine:
        raise ValueError("engine_class must subclass StrategyEngine")
    if str(getattr(cls, "engine_type", "")).upper() != manifest["engine_type"]:
        raise ValueError("engine class type does not match manifest")
    if str(getattr(cls, "engine_version", "")) != manifest["engine_version"]:
        raise ValueError("engine class version does not match manifest")
    cls.display_name = manifest["display_name"]
    cls.engine_grade = manifest["engine_grade"]
    cls.capabilities = tuple(manifest["capabilities"])
    cls.config_schema = dict(manifest["config_schema"])
    cls.package_origin = None
    cls.package_sha256 = info["sha256"]
    return cls, info


def load_dynamic_engine_classes() -> list[type]:
    """Load reviewed-local restricted engines from the persistent package store."""
    classes: list[type] = []
    root = package_store_root()
    if not root.exists():
        return classes
    candidates: dict[str, tuple[tuple[int, ...], Path]] = {}
    for package in sorted(root.glob("*/*/*.arbengine")):
        try:
            info = inspect_package_bytes(package.read_bytes())
            manifest = info["manifest"]
            key = manifest["engine_type"]; ver = _version_tuple(manifest["engine_version"])
            if key not in candidates or ver > candidates[key][0]:
                candidates[key] = (ver, package)
        except Exception:
            continue
    for _ver, package in sorted(candidates.values(), key=lambda x: str(x[1])):
        try:
            cls, _info = load_reviewed_engine_class(package.read_bytes())
            cls.package_origin = str(package)
            classes.append(cls)
        except Exception:
            # Bad packages remain inert on disk; import UI reports validation errors.
            continue
    return classes


def build_export_package(*, engine: Mapping[str, Any], type_meta: Mapping[str, Any], source_package: Path | None = None) -> bytes:
    """Build a portable package for an installed engine instance."""
    if source_package and source_package.exists():
        # Preserve the reviewed implementation while refreshing instance metadata.
        with zipfile.ZipFile(source_package, "r") as src:
            source = src.read("engine.py") if "engine.py" in src.namelist() else None
    else:
        source = None
    active = dict(engine.get("active_config") or {})
    manifest = _clean_manifest({
        "format_version": FORMAT_VERSION,
        "engine_api_version": ENGINE_API_VERSION,
        "engine_type": type_meta.get("engine_type") or engine.get("engine_type"),
        "display_name": type_meta.get("display_name") or engine.get("display_name"),
        "engine_version": type_meta.get("engine_version") or engine.get("engine_version"),
        "engine_grade": engine.get("engine_grade") or type_meta.get("engine_grade") or "RESEARCH",
        "capabilities": type_meta.get("capabilities") or engine.get("capabilities") or [],
        "config_schema": type_meta.get("config_schema") or engine.get("config_schema") or {},
        "default_config": active.get("config") or {},
        "description": engine.get("description") or "",
        "notes": engine.get("notes") or "",
        "author": engine.get("package_author") or "ArbScanner local export",
        "implementation_kind": "restricted_python" if source is not None else "builtin",
        "engine_class": type_meta.get("engine_class") or "Engine",
        "engine_instance_id": engine.get("engine_instance_id"),
        "section": engine.get("section") or "all", "sport": engine.get("sport") or "all",
        "competition": engine.get("competition") or "all", "market_type": engine.get("market_type") or "all",
    })
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        if source is not None:
            zf.writestr("engine.py", source)
    return buf.getvalue()
