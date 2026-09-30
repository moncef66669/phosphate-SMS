#!/usr/bin/env python3
"""Refresh or verify hashes in model_knowledge/model_manifest.json."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


def computed(root: Path, manifest: dict) -> tuple[str, list[tuple[dict, str]]]:
    sources = manifest["implementation"]["sources"]
    digest = hashlib.sha256()
    rows = []
    for row in sorted(sources, key=lambda value: value["path"]):
        relative = row["path"]
        data = (root / relative).read_bytes()
        source_hash = hashlib.sha256(data).hexdigest()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
        rows.append((row, source_hash))
    return digest.hexdigest(), rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    path = root / "model_knowledge" / "model_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    fingerprint, rows = computed(root, manifest)
    stale = manifest["implementation"].get("fingerprint") != fingerprint
    stale_sources = [row["path"] for row, digest in rows if row.get("sha256") != digest]
    if args.check:
        if stale or stale_sources:
            if stale:
                print("ERROR: implementation fingerprint is stale")
            for relative in stale_sources:
                print(f"ERROR: source hash is stale: {relative}")
            return 1
        print("OK: model manifest hashes are current")
        return 0
    for row, digest in rows:
        row["sha256"] = digest
    manifest["implementation"]["fingerprint"] = fingerprint
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Updated {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
