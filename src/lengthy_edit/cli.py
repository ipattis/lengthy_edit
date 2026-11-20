"""Command line entry point: ``lengthy-edit serve | new | list | render``."""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines); never overrides real env vars."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        os.environ.setdefault(key, val.strip().strip("'\""))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="lengthy-edit", description=__doc__)
    ap.add_argument("--home", help="workspace folder for projects (default: ./projects or $LENGTHY_EDIT_HOME)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the local web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--reload", action="store_true", help="auto-reload on code changes (dev)")

    n = sub.add_parser("new", help="create a project")
    n.add_argument("title")

    sub.add_parser("list", help="list projects")

    r = sub.add_parser("render", help="render a project to mp4")
    r.add_argument("slug")

    args = ap.parse_args(argv)
    load_dotenv(Path.cwd() / ".env")
    if args.home:
        os.environ["LENGTHY_EDIT_HOME"] = str(Path(args.home).expanduser().resolve())

    from . import store

    if args.cmd == "serve":
        import uvicorn

        url = f"http://{args.host}:{args.port}"
        print(f"lengthy-edit UI on {url}  (projects in {store.workspace_root()})")
        if not args.no_browser:
            webbrowser.open(url)
        uvicorn.run("lengthy_edit.server:app", host=args.host, port=args.port, reload=args.reload)
    elif args.cmd == "new":
        p = store.create(args.title)
        print(p.slug)
    elif args.cmd == "list":
        for p in store.list_projects():
            print(f"{p.slug:40s} {p.updated_at}  {p.status()}")
    elif args.cmd == "render":
        from . import render

        p = store.load(args.slug)
        rec = render.render_project(p, lambda m, f: print(f"[{f * 100:5.1f}%] {m}", file=sys.stderr))
        p = store.load(args.slug)
        p.renders.insert(0, rec)
        store.save(p)
        print(store.project_dir(p.slug) / rec.file)


if __name__ == "__main__":
    main()
