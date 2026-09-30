"""Load and query the versioned project model-knowledge package."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, Mapping, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = PROJECT_ROOT / "model_knowledge"
MANIFEST_PATH = PACKAGE_DIR / "model_manifest.json"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_fingerprint(manifest: Mapping[str, Any]) -> tuple[str, Dict[str, str]]:
    digest = hashlib.sha256()
    hashes: Dict[str, str] = {}
    sources = manifest.get("implementation", {}).get("sources", [])
    for row in sorted(sources, key=lambda value: str(value.get("path", ""))):
        relative = str(row["path"])
        data = (PROJECT_ROOT / relative).read_bytes()
        hashes[relative] = _sha256(data)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest(), hashes


def load_model_context() -> Dict[str, Any]:
    """Return a self-contained snapshot suitable for immutable run evidence."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    documents = {}
    document_hashes = {}
    for key, relative in manifest["documents"].items():
        data = (PROJECT_ROOT / relative).read_bytes()
        documents[key] = data.decode("utf-8")
        document_hashes[key] = _sha256(data)
    current_fingerprint, current_hashes = _source_fingerprint(manifest)
    declared_sources = {
        str(row.get("path")): str(row.get("sha256"))
        for row in manifest.get("implementation", {}).get("sources", [])
    }
    stale_sources = sorted(
        relative for relative, digest in current_hashes.items()
        if declared_sources.get(relative) != digest)
    declared_fingerprint = str(
        manifest.get("implementation", {}).get("fingerprint", ""))
    return {
        "schema_version": manifest.get("schema_version"),
        "project_id": manifest.get("project", {}).get("id"),
        "package_version": manifest.get("project", {}).get("package_version"),
        "last_verified": manifest.get("project", {}).get("last_verified"),
        "implementation_fingerprint": declared_fingerprint,
        "current_implementation_fingerprint": current_fingerprint,
        "package_current": (
            declared_fingerprint == current_fingerprint and not stale_sources),
        "stale_sources": stale_sources,
        "document_hashes": document_hashes,
        "manifest": manifest,
        "documents": documents,
    }


def _sections(markdown: str) -> Dict[str, str]:
    matches = list(re.finditer(r"^(#{1,6})\s+(.+?)\s*$", markdown, re.MULTILINE))
    sections: Dict[str, str] = {}
    for index, match in enumerate(matches):
        level = len(match.group(1))
        end = len(markdown)
        for following in matches[index + 1:]:
            if len(following.group(1)) <= level:
                end = following.start()
                break
        sections[match.group(2).strip()] = markdown[match.start():end].strip()
    return sections


def select_formulation_section(context: Mapping[str, Any], section: Optional[str]) -> Dict[str, Any]:
    text = str(context.get("documents", {}).get("formulation", ""))
    sections = _sections(text)
    if not section:
        return {"text": text, "available_sections": list(sections)}
    needle = str(section).casefold().strip()
    exact = [name for name in sections if name.casefold() == needle]
    matches = exact or [name for name in sections if needle in name.casefold()]
    if len(matches) != 1:
        raise KeyError(
            f"formulation section {section!r} matched {len(matches)} sections; "
            f"available: {', '.join(sections)}")
    return {
        "section": matches[0], "text": sections[matches[0]],
        "available_sections": list(sections),
    }


def find_definition(context: Mapping[str, Any], category: str,
                    identifier: str) -> Dict[str, Any]:
    rows = context.get("manifest", {}).get(category, [])
    needle = str(identifier).casefold().strip()
    exact = [row for row in rows if str(row.get("id", "")).casefold() == needle]
    if len(exact) == 1:
        return copy.deepcopy(exact[0])
    matches = [row for row in rows if needle in {
        str(row.get("name", "")).casefold(),
        str(row.get("symbol", "")).casefold(),
    }]
    if len(matches) != 1:
        available = ", ".join(str(row.get("id")) for row in rows)
        raise KeyError(
            f"{category} definition {identifier!r} matched {len(matches)} rows; "
            f"use a stable id: {available}")
    return copy.deepcopy(matches[0])


def find_code_mapping(context: Mapping[str, Any], identifier: str) -> Dict[str, Any]:
    needle = str(identifier).casefold().strip()
    manifest = context.get("manifest", {})
    mappings = manifest.get("code_mappings", [])
    matches = [row for row in mappings
               if str(row.get("id", "")).casefold() == needle]
    if matches:
        return {"mapping": copy.deepcopy(matches[0]), "source_category": "code_mappings"}
    related = []
    for category in ("parameters", "variables", "objective_terms", "constraints", "kpis"):
        for row in manifest.get(category, []):
            if str(row.get("id", "")).casefold() == needle:
                related.append({"category": category, "definition": copy.deepcopy(row)})
    if not related:
        available = sorted(
            str(row.get("id")) for category in
            ("parameters", "variables", "objective_terms", "constraints", "kpis", "code_mappings")
            for row in manifest.get(category, []))
        raise KeyError(
            f"code mapping {identifier!r} was not found; stable ids: "
            + ", ".join(available))
    return {"mapping": related, "source_category": "definition_code_refs"}
