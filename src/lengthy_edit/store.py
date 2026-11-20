"""Filesystem persistence: one folder per project under a workspace root.

    <workspace>/<slug>/project.json
    <workspace>/<slug>/song/...
    <workspace>/<slug>/shots/<shot-id>/{image,video}.*
    <workspace>/<slug>/sound/...
    <workspace>/<slug>/renders/...
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from .models import Project

_lock = threading.RLock()


def workspace_root() -> Path:
    root = Path(os.environ.get("LENGTHY_EDIT_HOME", Path.cwd() / "projects")).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "untitled"


def project_dir(slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        raise ValueError(f"invalid project slug: {slug!r}")
    return workspace_root() / slug


def safe_path(slug: str, rel: str) -> Path:
    """Resolve ``rel`` inside the project dir, refusing path traversal."""
    base = project_dir(slug).resolve()
    p = (base / rel).resolve()
    if base != p and base not in p.parents:
        raise ValueError(f"path escapes project: {rel!r}")
    return p


def list_projects() -> list[Project]:
    out = []
    for d in sorted(workspace_root().iterdir()):
        if (d / "project.json").is_file():
            try:
                out.append(load(d.name))
            except Exception:  # corrupt file: skip, don't break the listing
                continue
    return sorted(out, key=lambda p: p.updated_at, reverse=True)


def create(title: str) -> Project:
    with _lock:
        base = slugify(title)
        slug, n = base, 2
        while (workspace_root() / slug).exists():
            slug, n = f"{base}-{n}", n + 1
        p = Project(slug=slug)
        p.brief.title = title
        project_dir(slug).mkdir(parents=True)
        save(p)
        return p


def load(slug: str) -> Project:
    path = project_dir(slug) / "project.json"
    if not path.is_file():
        raise FileNotFoundError(slug)
    return Project.model_validate_json(path.read_text())


def save(p: Project) -> Project:
    with _lock:
        p.touch()
        d = project_dir(p.slug)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "project.json.tmp"
        tmp.write_text(json.dumps(p.model_dump(mode="json"), indent=2))
        tmp.replace(d / "project.json")
        return p
