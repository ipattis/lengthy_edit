"""Assemble the film with ffmpeg.

Each shot becomes a normalized H.264 segment of its exact duration (cached by
content hash, so re-renders of a long edit only re-encode changed shots).
Shots without assets become labelled placeholder cards, which makes an animatic
possible as soon as the storyboard and song exist. Segments are concatenated, then
the song and sound-layer cues are mixed underneath.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFont

from . import store
from .models import Project, RenderRecord, RenderSettings, Shot, ShotKind

Progress = Callable[[str, float], None]

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


class RenderError(RuntimeError):
    pass


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RenderError(f"ffmpeg failed: {' '.join(cmd[:6])} ...\n{tail}")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise RenderError(f"could not read duration of {path.name}") from e


# --------------------------------------------------------------------------- cards


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def make_card(path: Path, w: int, h: int, text: str, *, placeholder: bool, subtitle: str = "") -> Path:
    img = Image.new("RGB", (w, h), (18, 18, 22) if placeholder else (0, 0, 0))
    d = ImageDraw.Draw(img)
    main = _font(int(h * (0.045 if placeholder else 0.08)))
    small = _font(int(h * 0.028))
    wrapped = textwrap.fill(text, width=40 if placeholder else 28)
    box = d.multiline_textbbox((0, 0), wrapped, font=main, align="center", spacing=12)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.multiline_text(((w - tw) / 2, (h - th) / 2), wrapped, font=main, fill=(235, 235, 235), align="center", spacing=12)
    if subtitle:
        d.text((int(w * 0.04), int(h * 0.92)), subtitle, font=small, fill=(140, 140, 150))
    if placeholder:
        d.rectangle([8, 8, w - 9, h - 9], outline=(70, 70, 80), width=3)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


# --------------------------------------------------------------------------- segments


def _source(p: Project, shot: Shot) -> tuple[str, Optional[Path]]:
    """Return (mode, path) where mode is video | still | title | placeholder."""
    if shot.kind == ShotKind.title:
        return "title", None
    if shot.video:
        f = store.safe_path(p.slug, shot.video)
        if f.is_file():
            return "video", f
    if shot.image:
        f = store.safe_path(p.slug, shot.image)
        if f.is_file():
            return "still", f
    return "placeholder", None


def _segment_key(shot: Shot, mode: str, src: Optional[Path], rs: RenderSettings) -> str:
    h = hashlib.sha1()
    parts = [mode, f"{shot.duration_s:.3f}", rs.width, rs.height, rs.fps, rs.crf, shot.ken_burns]
    if src:
        st = src.stat()
        parts += [str(src), st.st_size, st.st_mtime_ns]
    if mode in ("title", "placeholder"):
        parts += [shot.on_screen_text, shot.description, shot.id, shot.kind.value]
    h.update(json.dumps(parts, default=str).encode())
    return h.hexdigest()[:16]


def _encode_args(rs: RenderSettings) -> list[str]:
    return ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(rs.crf), "-pix_fmt", "yuv420p", "-r", str(rs.fps)]


def render_segment(p: Project, shot: Shot, out_dir: Path) -> tuple[Path, bool]:
    """Encode one shot. Returns (segment path, used_placeholder)."""
    rs = p.render
    w, h, fps = rs.width, rs.height, rs.fps
    mode, src = _source(p, shot)
    out = out_dir / f"{shot.id}-{_segment_key(shot, mode, src, rs)}.mp4"
    if out.is_file():
        return out, mode == "placeholder"

    frames = max(1, round(shot.duration_s * fps))
    dur = f"{frames / fps:.3f}"
    cover = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"

    if mode == "video":
        cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src), "-t", dur, "-vf", f"{cover},fps={fps},format=yuv420p"]
    elif mode == "still" and shot.ken_burns:
        # Slow centred push-in; scale up first so zoompan doesn't jitter.
        big = f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2}"
        zoom = (
            f"zoompan=z='1+0.10*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={w}x{h}:fps={fps}"
        )
        cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", f"{big},{zoom},setsar=1,format=yuv420p", "-frames:v", str(frames)]
    else:
        if mode == "still":
            img = src
        else:
            card_dir = out_dir / "cards"
            if mode == "title":
                img = make_card(card_dir / f"{shot.id}-title.png", w, h, shot.on_screen_text or shot.description, placeholder=False)
            else:
                img = make_card(
                    card_dir / f"{shot.id}-ph.png",
                    w,
                    h,
                    shot.description or shot.id,
                    placeholder=True,
                    subtitle=f"{shot.id} · {shot.kind.value} · {shot.section} · {shot.start_s:.1f}s",
                )
        vf = f"{cover},format=yuv420p"
        if mode == "title":
            fade = min(0.4, shot.duration_s / 4)
            vf += f",fade=t=in:st=0:d={fade:.2f},fade=t=out:st={shot.duration_s - fade:.2f}:d={fade:.2f}"
        cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(fps), "-i", str(img), "-t", dur, "-vf", vf]

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    _run(cmd + _encode_args(rs) + [str(tmp)])
    tmp.replace(out)
    return out, mode == "placeholder"


# --------------------------------------------------------------------------- audio


def _audio_inputs(p: Project, total: float) -> tuple[list[str], str] | None:
    """Return (input args, filter_complex) mixing the song with sound cues."""
    inputs: list[str] = []
    labels: list[str] = []
    filters: list[str] = []
    idx = 1  # input 0 is the concatenated video
    if p.song.file:
        f = store.safe_path(p.slug, p.song.file)
        if f.is_file():
            inputs += ["-i", str(f)]
            filters.append(f"[{idx}:a]volume={p.render.song_gain_db}dB,atrim=0:{total:.3f}[a{idx}]")
            labels.append(f"[a{idx}]")
            idx += 1
    for cue in p.sound_cues:
        if not cue.file or cue.at_s >= total:
            continue
        f = store.safe_path(p.slug, cue.file)
        if not f.is_file():
            continue
        ms = int(cue.at_s * 1000)
        inputs += ["-i", str(f)]
        filters.append(
            f"[{idx}:a]atrim=0:{cue.duration_s:.3f},afade=t=out:st={max(0, cue.duration_s - 0.3):.3f}:d=0.3,"
            f"volume={cue.gain_db}dB,adelay={ms}|{ms}[a{idx}]"
        )
        labels.append(f"[a{idx}]")
        idx += 1
    if not labels:
        return None
    if len(labels) == 1:
        filters.append(f"{labels[0]}apad,atrim=0:{total:.3f}[aout]")
    else:
        filters.append(
            f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0,apad,atrim=0:{total:.3f}[aout]"
        )
    return inputs, ";".join(filters)


# --------------------------------------------------------------------------- main


def render_project(p: Project, progress: Progress = lambda m, f: None) -> RenderRecord:
    if not ffmpeg_available():
        raise RenderError("ffmpeg/ffprobe not found on PATH")
    if not p.shots:
        raise RenderError("storyboard is empty")

    pdir = store.project_dir(p.slug)
    seg_dir = pdir / "renders" / "segments"
    shots = sorted(p.shots, key=lambda s: s.start_s)
    segments: list[Path] = []
    any_placeholder = False
    for i, shot in enumerate(shots):
        progress(f"Encoding {shot.id} ({i + 1}/{len(shots)})", 0.85 * i / len(shots))
        seg, ph = render_segment(p, shot, seg_dir)
        segments.append(seg)
        any_placeholder |= ph

    progress("Concatenating", 0.88)
    concat_list = pdir / "renders" / "concat.txt"
    concat_list.write_text("".join(f"file '{s.resolve().as_posix()}'\n" for s in segments))
    video_only = pdir / "renders" / "video_only.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(video_only)])

    total = sum(max(1, round(s.duration_s * p.render.fps)) for s in shots) / p.render.fps
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{p.slug}-{'animatic-' if any_placeholder else ''}{stamp}.mp4"
    final = pdir / "renders" / name

    progress("Mixing audio", 0.93)
    audio = _audio_inputs(p, total)
    if audio is None:
        shutil.copyfile(video_only, final)
    else:
        inputs, fc = audio
        _run(
            ["ffmpeg", "-y", "-i", str(video_only), *inputs, "-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(final)]
        )
    video_only.unlink(missing_ok=True)
    progress("Done", 1.0)
    return RenderRecord(file=f"renders/{name}", animatic=any_placeholder)
