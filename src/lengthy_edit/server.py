"""FastAPI app: JSON API + static single-page UI."""

from __future__ import annotations

import csv
import io
import os
import time
from importlib import resources
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, claude, higgsfield, render, store, tasks
from .models import Brief, Lyrics, Project, RenderSettings, Shot, ShotKind, SoundCue

STATIC = Path(str(resources.files("lengthy_edit") / "static"))

app = FastAPI(title="lengthy-edit", version=__version__)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# --------------------------------------------------------------------------- helpers


def _load(slug: str) -> Project:
    try:
        return store.load(slug)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, f"project {slug!r} not found")


def _mutate(slug: str, fn) -> Project:
    """Load-modify-save under the store lock so background jobs don't clobber edits."""
    with store._lock:
        p = _load(slug)
        fn(p)
        return store.save(p)


def _dump(p: Project) -> dict:
    d = p.model_dump(mode="json")
    d["status"] = p.status()
    d["timeline_duration"] = p.timeline_duration()
    return d


def _shot(p: Project, shot_id: str) -> Shot:
    try:
        return p.shot(shot_id)
    except KeyError:
        raise HTTPException(404, f"shot {shot_id!r} not found")


# --------------------------------------------------------------------------- meta


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {
        "version": __version__,
        "model": claude.MODEL,
        "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
        "higgsfield": higgsfield.configured(),
        "ffmpeg": render.ffmpeg_available(),
        "workspace": str(store.workspace_root()),
    }


# --------------------------------------------------------------------------- projects


class NewProject(BaseModel):
    title: str


@app.get("/api/projects")
def list_projects() -> list[dict]:
    return [
        {"slug": p.slug, "title": p.brief.title, "updated_at": p.updated_at, "status": p.status()}
        for p in store.list_projects()
    ]


@app.post("/api/projects")
def create_project(body: NewProject) -> dict:
    if not body.title.strip():
        raise HTTPException(400, "title required")
    return _dump(store.create(body.title.strip()))


@app.get("/api/projects/{slug}")
def get_project(slug: str) -> dict:
    return _dump(_load(slug))


@app.put("/api/projects/{slug}/brief")
def put_brief(slug: str, body: Brief) -> dict:
    return _dump(_mutate(slug, lambda p: setattr(p, "brief", body)))


class ResearchBody(BaseModel):
    research: str


@app.put("/api/projects/{slug}/research")
def put_research(slug: str, body: ResearchBody) -> dict:
    return _dump(_mutate(slug, lambda p: setattr(p, "research", body.research)))


@app.put("/api/projects/{slug}/lyrics")
def put_lyrics(slug: str, body: Lyrics) -> dict:
    return _dump(_mutate(slug, lambda p: setattr(p, "lyrics", body)))


@app.put("/api/projects/{slug}/render-settings")
def put_render_settings(slug: str, body: RenderSettings) -> dict:
    return _dump(_mutate(slug, lambda p: setattr(p, "render", body)))


@app.post("/api/projects/{slug}/lyrics/auto-time")
def auto_time_sections(slug: str) -> dict:
    """Spread section timings across the song, weighted by line count. A starting
    point; refine by ear in the UI."""

    def fn(p: Project) -> None:
        total = p.song.duration_s or float(p.brief.target_duration_s)
        weights = [max(2, len(s.lines)) for s in p.lyrics.sections]
        if not weights:
            raise HTTPException(400, "no lyric sections")
        t = 0.0
        for s, w in zip(p.lyrics.sections, weights):
            d = total * w / sum(weights)
            s.start_s, s.end_s = round(t, 2), round(t + d, 2)
            t += d

    return _dump(_mutate(slug, fn))


# --------------------------------------------------------------------------- Claude stages

STAGES = {"research", "lyrics", "storyboard", "sound"}


class GenerateBody(BaseModel):
    web_search: bool = True


@app.post("/api/projects/{slug}/generate/{stage}")
def generate(slug: str, stage: str, body: GenerateBody | None = None) -> dict:
    if stage not in STAGES:
        raise HTTPException(404, f"unknown stage {stage}")
    if tasks.running(slug, stage):
        raise HTTPException(409, f"{stage} generation already running")
    p = _load(slug)
    if not (p.brief.title and p.brief.concept):
        raise HTTPException(400, "fill in at least the brief's title and concept first")
    web_search = body.web_search if body else True

    def job(progress) -> None:
        progress(f"Claude is working on {stage}…", 0.1)
        if stage == "research":
            text = claude.run_research(p, web_search=web_search)
            _mutate(slug, lambda q: setattr(q, "research", text))
        elif stage == "lyrics":
            lyr = claude.run_lyrics(p)
            _mutate(slug, lambda q: setattr(q, "lyrics", lyr))
        elif stage == "storyboard":
            shots = claude.run_storyboard(p)
            _mutate(slug, lambda q: setattr(q, "shots", shots))
        elif stage == "sound":
            cues = claude.run_sound(p)
            _mutate(slug, lambda q: setattr(q, "sound_cues", cues))

    return tasks.submit(stage, slug, job)


class RefineBody(BaseModel):
    note: str


@app.post("/api/projects/{slug}/shots/{shot_id}/refine")
def refine(slug: str, shot_id: str, body: RefineBody) -> dict:
    p = _load(slug)
    shot = _shot(p, shot_id)

    def job(progress) -> None:
        progress(f"Refining {shot_id}…", 0.1)
        revised = claude.refine_shot(p, shot, body.note)

        def apply(q: Project) -> None:
            cur = _shot(q, shot_id)
            for k in ("section", "kind", "description", "camera", "lyric", "midjourney_prompt",
                      "motion_prompt", "on_screen_text", "duration_s"):
                setattr(cur, k, getattr(revised, k))
            q.shots = claude.normalize_timeline(q.shots, q.song.duration_s)

        _mutate(slug, apply)

    return tasks.submit(f"refine:{shot_id}", slug, job)


@app.get("/api/projects/{slug}/tasks")
def project_tasks(slug: str) -> list[dict]:
    return tasks.for_project(slug)


@app.get("/api/tasks/{tid}")
def get_task(tid: str) -> dict:
    try:
        return tasks.get(tid)
    except KeyError:
        raise HTTPException(404, "task not found")


# --------------------------------------------------------------------------- shots

EDITABLE_SHOT_FIELDS = {
    "section", "start_s", "duration_s", "kind", "description", "camera", "lyric",
    "midjourney_prompt", "motion_prompt", "on_screen_text", "ken_burns",
}


@app.patch("/api/projects/{slug}/shots/{shot_id}")
def patch_shot(slug: str, shot_id: str, body: dict[str, Any]) -> dict:
    bad = set(body) - EDITABLE_SHOT_FIELDS
    if bad:
        raise HTTPException(400, f"not editable: {sorted(bad)}")

    def fn(p: Project) -> None:
        s = _shot(p, shot_id)
        merged = Shot.model_validate(s.model_dump() | body)
        for k in body:
            setattr(s, k, getattr(merged, k))
        if "duration_s" in body or "kind" in body:
            p.shots = claude.normalize_timeline(p.shots, p.song.duration_s)

    return _dump(_mutate(slug, fn))


class NewShot(BaseModel):
    after: Optional[str] = None
    kind: ShotKind = ShotKind.still
    duration_s: float = 4.0


@app.post("/api/projects/{slug}/shots")
def add_shot(slug: str, body: NewShot) -> dict:
    def fn(p: Project) -> None:
        n = 1 + max((int(s.id[1:]) for s in p.shots if s.id[1:].isdigit()), default=0)
        new = Shot(id=f"s{n:03d}", kind=body.kind, duration_s=body.duration_s)
        idx = len(p.shots)
        if body.after:
            idx = next((i + 1 for i, s in enumerate(p.shots) if s.id == body.after), idx)
            new.start_s = p.shots[idx - 1].end_s if idx else 0
            new.section = p.shots[idx - 1].section if idx else ""
        else:
            new.start_s = p.timeline_duration()
        p.shots.insert(idx, new)
        # Insert shifts later shots; don't stretch the last one to the song end.
        p.shots = claude.normalize_timeline(p.shots, None)

    return _dump(_mutate(slug, fn))


@app.delete("/api/projects/{slug}/shots/{shot_id}")
def delete_shot(slug: str, shot_id: str) -> dict:
    def fn(p: Project) -> None:
        _shot(p, shot_id)
        p.shots = claude.normalize_timeline([s for s in p.shots if s.id != shot_id], None)

    return _dump(_mutate(slug, fn))


@app.post("/api/projects/{slug}/shots/fit")
def fit_shots(slug: str) -> dict:
    """Re-tile shots contiguously and stretch the last one to the song end."""
    return _dump(_mutate(slug, lambda p: setattr(p, "shots", claude.normalize_timeline(p.shots, p.song.duration_s))))


@app.delete("/api/projects/{slug}/shots/{shot_id}/asset/{which}")
def clear_asset(slug: str, shot_id: str, which: str) -> dict:
    if which not in ("image", "video"):
        raise HTTPException(400, "which must be image or video")
    return _dump(_mutate(slug, lambda p: setattr(_shot(p, shot_id), which, None)))


# --------------------------------------------------------------------------- Seedance


def _poll_motion(slug: str, shot_id: str, status_url: str, progress) -> None:
    deadline = time.time() + 60 * 30
    while time.time() < deadline:
        st = higgsfield.status(status_url)
        state = st.get("status", "unknown")
        _mutate(slug, lambda q: setattr(_shot(q, shot_id), "job_status", state))
        progress(f"Seedance {shot_id}: {state}", 0.5)
        if state in higgsfield.TERMINAL:
            if state != "completed":
                err = st.get("error") or state
                _mutate(slug, lambda q: setattr(_shot(q, shot_id), "job_error", str(err)))
                raise higgsfield.HiggsfieldError(f"Seedance {shot_id}: {err}")
            url = (st.get("video") or {}).get("url")
            if not url:
                raise higgsfield.HiggsfieldError("completed without a video URL")
            rel = f"shots/{shot_id}/seedance-{int(time.time())}.mp4"
            higgsfield.download(url, store.safe_path(slug, rel))

            def done(q: Project) -> None:
                s = _shot(q, shot_id)
                s.video, s.job_error = rel, None

            _mutate(slug, done)
            return
        time.sleep(8)
    raise higgsfield.HiggsfieldError("timed out waiting for Seedance")


@app.post("/api/projects/{slug}/shots/{shot_id}/motion")
def generate_motion(slug: str, shot_id: str) -> dict:
    if not higgsfield.configured():
        raise HTTPException(400, "Higgsfield is not configured (HF_API_KEY_ID / HF_API_KEY_SECRET)")
    if tasks.running(slug, f"motion:{shot_id}"):
        raise HTTPException(409, "already generating")
    p = _load(slug)
    shot = _shot(p, shot_id)

    def job(progress) -> None:
        image_url = None
        if shot.image:
            progress(f"Uploading still for {shot_id}", 0.1)
            image_url = higgsfield.upload(store.safe_path(slug, shot.image))
        progress(f"Submitting {shot_id} to Seedance", 0.2)
        res = higgsfield.submit_motion(
            prompt=shot.motion_prompt or shot.description,
            duration_s=shot.duration_s,
            image_url=image_url,
            aspect_ratio=p.brief.aspect_ratio,
        )

        def started(q: Project) -> None:
            s = _shot(q, shot_id)
            s.job_id, s.job_status_url = res.get("request_id"), res.get("status_url")
            s.job_status, s.job_error = res.get("status", "queued"), None

        _mutate(slug, started)
        _poll_motion(slug, shot_id, res["status_url"], progress)

    return tasks.submit(f"motion:{shot_id}", slug, job)


@app.post("/api/projects/{slug}/motion/all")
def generate_all_motion(slug: str) -> list[dict]:
    """Queue Seedance for every motion shot that has no video yet."""
    p = _load(slug)
    out = []
    for s in p.shots:
        if s.kind == ShotKind.motion and not s.video and not tasks.running(slug, f"motion:{s.id}"):
            out.append(generate_motion(slug, s.id))
    return out


# --------------------------------------------------------------------------- uploads

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v", ".gif"}


@app.post("/api/projects/{slug}/upload")
async def upload(
    slug: str,
    target: str = Query(..., pattern="^(song|shot_image|shot_video|cue)$"),
    id: Optional[str] = None,
    file: UploadFile = File(...),
) -> dict:
    p = _load(slug)
    ext = Path(file.filename or "").suffix.lower()
    allowed = {"song": AUDIO_EXT, "cue": AUDIO_EXT, "shot_image": IMAGE_EXT, "shot_video": VIDEO_EXT}[target]
    if ext not in allowed:
        raise HTTPException(400, f"{ext or 'file'} not allowed for {target}; use {sorted(allowed)}")
    if target != "song":
        if not id:
            raise HTTPException(400, "id required")
        if target == "cue":
            if not any(c.id == id for c in p.sound_cues):
                raise HTTPException(404, f"cue {id!r} not found")
        else:
            _shot(p, id)

    stamp = int(time.time())
    rel = {
        "song": f"song/song-{stamp}{ext}",
        "shot_image": f"shots/{id}/image-{stamp}{ext}",
        "shot_video": f"shots/{id}/video-{stamp}{ext}",
        "cue": f"sound/{id}-{stamp}{ext}",
    }[target]
    dest = store.safe_path(slug, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)

    def fn(q: Project) -> None:
        if target == "song":
            q.song.file = rel
            try:
                q.song.duration_s = round(render.probe_duration(dest), 3)
            except render.RenderError:
                q.song.duration_s = None
        elif target == "cue":
            next(c for c in q.sound_cues if c.id == id).file = rel
        else:
            setattr(_shot(q, id), "image" if target == "shot_image" else "video", rel)

    return _dump(_mutate(slug, fn))


# --------------------------------------------------------------------------- sound cues


@app.put("/api/projects/{slug}/sound-cues")
def put_cues(slug: str, body: list[SoundCue]) -> dict:
    def fn(p: Project) -> None:
        # Keep file links the client may not know about.
        files = {c.id: c.file for c in p.sound_cues}
        for c in body:
            if c.file is None:
                c.file = files.get(c.id)
        p.sound_cues = sorted(body, key=lambda c: c.at_s)

    return _dump(_mutate(slug, fn))


# --------------------------------------------------------------------------- render


@app.post("/api/projects/{slug}/render")
def start_render(slug: str) -> dict:
    if tasks.running(slug, "render"):
        raise HTTPException(409, "render already running")
    _load(slug)

    def job(progress) -> None:
        rec = render.render_project(_load(slug), progress)
        _mutate(slug, lambda q: q.renders.insert(0, rec))

    return tasks.submit("render", slug, job)


# --------------------------------------------------------------------------- exports


@app.get("/api/projects/{slug}/export/prompts.csv")
def export_prompts_csv(slug: str) -> Response:
    p = _load(slug)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "start_s", "duration_s", "kind", "section", "lyric", "midjourney_prompt", "motion_prompt", "on_screen_text"])
    for s in p.shots:
        w.writerow([s.id, s.start_s, s.duration_s, s.kind.value, s.section, s.lyric, s.midjourney_prompt, s.motion_prompt, s.on_screen_text])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{slug}-prompts.csv"'})


@app.get("/api/projects/{slug}/export/suno.txt", response_class=PlainTextResponse)
def export_suno(slug: str) -> str:
    p = _load(slug)
    return f"Title: {p.lyrics.suno_title}\nStyle: {p.lyrics.suno_style}\n\n{p.lyrics.as_suno_text()}\n"


@app.get("/files/{slug}/{path:path}")
def project_file(slug: str, path: str) -> FileResponse:
    try:
        f = store.safe_path(slug, path)
    except ValueError:
        raise HTTPException(400, "bad path")
    if not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)
