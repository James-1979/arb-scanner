#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def run_probe(probe: Path, code_root: Path, db: Path, manifest: Path, output: Path, home: Path, anchor: str) -> None:
    cmd = [sys.executable, str(probe), "--code-root", str(code_root), "--db", str(db), "--manifest", str(manifest), "--output", str(output), "--home", str(home), "--anchor", anchor]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"Probe failed for {code_root}: {proc.stderr or proc.stdout}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare ArbScanner Reference and Candidate read projections on the same DB snapshot.")
    parser.add_argument("--reference-root", required=True)
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--manifest", default="validation/refactor_projection_manifest.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--anchor")
    args = parser.parse_args()

    candidate_root = Path(args.candidate_root).resolve()
    reference_root = Path(args.reference_root).resolve()
    manifest = Path(args.manifest)
    if not manifest.is_absolute():
        manifest = candidate_root / manifest
    probe = candidate_root / "scripts" / "refactor_probe.py"
    anchor = args.anchor or datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix="arbscanner-compare-") as tmp:
        tmp = Path(tmp)
        ref_out = tmp / "reference.json"
        cand_out = tmp / "candidate.json"
        run_probe(probe, reference_root, Path(args.db).resolve(), manifest, ref_out, tmp / "home-reference", anchor)
        run_probe(probe, candidate_root, Path(args.db).resolve(), manifest, cand_out, tmp / "home-candidate", anchor)
        reference = json.loads(ref_out.read_text())
        candidate = json.loads(cand_out.read_text())

    ref_by_id = {x["id"]: x for x in reference.get("projections") or []}
    cand_by_id = {x["id"]: x for x in candidate.get("projections") or []}
    rows = []
    blockers = []
    warnings = []
    for projection_id in sorted(set(ref_by_id) | set(cand_by_id)):
        ref = ref_by_id.get(projection_id)
        cand = cand_by_id.get(projection_id)
        issues = []
        level = "PASS"
        if ref is None or cand is None:
            issues.append("projection missing from one build")
            level = "BLOCK"
        else:
            if ref.get("error") or cand.get("error"):
                issues.append(f"execution error reference={ref.get('error')} candidate={cand.get('error')}")
                level = "BLOCK"
            if ref.get("output_fingerprint") != cand.get("output_fingerprint"):
                issues.append("output fingerprint mismatch")
                level = "BLOCK"
            cand_authority = int((cand.get("write_classes") or {}).get("authority", 0))
            if cand_authority:
                issues.append(f"candidate authority writes={cand_authority}")
                level = "BLOCK"
            if int(cand.get("write_statement_count") or 0) > int(ref.get("write_statement_count") or 0):
                issues.append(f"read-path writes increased {ref.get('write_statement_count')} -> {cand.get('write_statement_count')}")
                level = "BLOCK"
            ref_q = int(ref.get("query_count") or 0)
            cand_q = int(cand.get("query_count") or 0)
            query_budget = ref_q + max(3, int(math.ceil(ref_q * 0.10)))
            if cand_q > query_budget:
                issues.append(f"query regression {ref_q} -> {cand_q} (budget {query_budget})")
                level = "BLOCK"
            elif cand_q > ref_q:
                issues.append(f"query increase {ref_q} -> {cand_q}")
                if level == "PASS":
                    level = "WARN"
        row = {
            "id": projection_id,
            "status": level,
            "issues": issues,
            "reference": None if ref is None else {k: ref.get(k) for k in ("method", "query_count", "write_statement_count", "total_changes", "write_tables", "write_classes", "elapsed_ms", "output_fingerprint")},
            "candidate": None if cand is None else {k: cand.get(k) for k in ("method", "query_count", "write_statement_count", "total_changes", "write_tables", "write_classes", "elapsed_ms", "output_fingerprint")}
        }
        rows.append(row)
        if level == "BLOCK":
            blockers.append(projection_id)
        elif level == "WARN":
            warnings.append(projection_id)

    payload = {
        "schema_version": 1,
        "anchor": anchor,
        "reference_root": str(reference_root),
        "candidate_root": str(candidate_root),
        "source_db": str(Path(args.db).resolve()),
        "summary": {"projections": len(rows), "pass": sum(x["status"] == "PASS" for x in rows), "warnings": len(warnings), "blockers": len(blockers)},
        "blockers": blockers,
        "warnings": warnings,
        "rows": rows
    }
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload["summary"], sort_keys=True))
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
