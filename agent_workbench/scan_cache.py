"""Disposable display snapshots and content-validated in-memory parse results."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .adapters import parse_document
from .storage import read_json, write_json

SCHEMA = 1


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def load_snapshot(path: Path, context: str):
    try:
        cached = read_json(path, {})
        snapshot = cached["snapshot"]
        if (
            cached["schema"] != SCHEMA
            or cached["context"] != context
            or cached["checksum"] != fingerprint(snapshot)
            or not isinstance(snapshot, dict)
            or not isinstance(snapshot["counts"], dict)
            or not isinstance(snapshot["scanned_at"], str)
            or any(
                not isinstance(snapshot[name], list)
                for name in ("agents", "skills", "mcps", "roles", "issues")
            )
        ):
            return None
        return snapshot
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_snapshot(path: Path, context: str, snapshot):
    # Only the public catalog is persisted: no raw MCP credentials or Skill bodies.
    write_json(
        path,
        {
            "schema": SCHEMA,
            "context": context,
            "checksum": fingerprint(snapshot),
            "snapshot": snapshot,
        },
    )


class ParsedFiles:
    def __init__(self):
        self.entries = {}
        self.seen = set()

    def begin(self):
        self.seen.clear()

    def prune(self):
        self.entries = {k: v for k, v in self.entries.items() if k in self.seen}

    def read(self, path: Path, kind: str, parser):
        # Read and hash on each scan. Same-size edits with restored timestamps,
        # replaced files, and changed symlink targets must still be detected.
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        key = (str(path), kind)
        self.seen.add(key)
        previous = self.entries.get(key)
        if previous and previous[0] == digest:
            return previous[1], digest
        value = parser(content)
        self.entries[key] = (digest, value)
        return value, digest

    def document(self, path: Path, kind: str):
        return self.read(path, kind, lambda content: parse_document(content, kind))
