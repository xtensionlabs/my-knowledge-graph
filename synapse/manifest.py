"""Synapse manifest — per-repo `synapse.json`.

Every code repository that installs the Synapse git hook can optionally carry
a `synapse.json` at its root.  The Librarian (M3 sweep) reads this file to:

    - Identify which CONCEPT nodes this BUILD relates to (`cs_concepts`)
    - Surface open engineering questions (`open_questions`)
    - Keep `last_commit` + `synapse_node_id` in sync automatically

The manifest is written by `synapse hooks install` (stub fields) and kept
up to date by `GitIngestAgent` after every commit.

Schema reference: SYNAPSE_PRD.md §5.4 (BUILD node fields)
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from synapse.config import SYNAPSE_MANIFEST_FILENAME


class SynapseManifest(BaseModel):
    """Schema for `synapse.json` at the root of a tracked repository.

    Fields:
        module:          Short kebab-case name for the repo / module.
        description:     One-line human description.
        cs_concepts:     Titles of CONCEPT nodes this build applies to.
        open_questions:  Free-text engineering questions (surfaced by Synthesizer).
        last_commit:     SHA of the last commit ingested by Synapse.
        synapse_node_id: UUID of the BUILD node in the knowledge graph.
    """

    module: str = Field(default="", description="Short kebab-case repo / module name.")
    description: str = Field(default="", description="One-line human description.")
    cs_concepts: list[str] = Field(
        default_factory=list,
        description="Titles of CONCEPT nodes this build applies to or implements.",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="Open engineering questions surfaced to the Synthesizer.",
    )
    last_commit: str = Field(default="", description="SHA of the last ingested commit.")
    synapse_node_id: str = Field(
        default="", description="UUID of this repo's BUILD node in the knowledge graph."
    )


def read_manifest(repo_path: Path) -> SynapseManifest | None:
    """Read `synapse.json` from `repo_path`. Returns None if absent or invalid.

    Args:
        repo_path: Root directory of the target repository.

    Returns:
        Parsed manifest, or None if the file is missing / malformed.
    """
    manifest_path = repo_path / SYNAPSE_MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return SynapseManifest.model_validate(data)
    except (json.JSONDecodeError, ValueError, OSError):
        return None


def write_manifest(repo_path: Path, manifest: SynapseManifest) -> None:
    """Atomically write `synapse.json` to `repo_path`.

    Uses temp-file + os.replace for crash safety.

    Args:
        repo_path: Root directory of the target repository.
        manifest:  The manifest to serialise.
    """
    import os
    import tempfile

    manifest_path = repo_path / SYNAPSE_MANIFEST_FILENAME
    data = manifest.model_dump(mode="json")
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    # Atomic write: temp file in same directory, then replace.
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=repo_path,
        delete=False,
        suffix=".tmp",
    ) as tmp:
        tmp.write(text)
        tmp_path = tmp.name
    os.replace(tmp_path, manifest_path)
