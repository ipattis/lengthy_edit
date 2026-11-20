"""Claude-driven stages: research, lyrics, storyboard, sound layer, shot refinement."""

from __future__ import annotations

import json
import os
from typing import Any

import anthropic

from . import prompts
from .models import LyricSection, Lyrics, Project, Shot, ShotKind, SoundCue

MODEL = os.environ.get("LENGTHY_EDIT_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("LENGTHY_EDIT_EFFORT", "high")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeError(RuntimeError):
    pass


_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        # Resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login` profile.
        _client = anthropic.Anthropic()
    return _client


def _call(
    instructions: str,
    context: str,
    *,
    schema: dict | None = None,
    tools: list[dict] | None = None,
    max_continuations: int = 5,
) -> str:
    """Run one stage. Returns the concatenated text of the final answer.

    Streams (outputs can be long), opts into server-side refusal fallbacks, and
    resumes ``pause_turn`` when server tools (web search) run long.
    """
    output_config: dict[str, Any] = {"effort": EFFORT}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}

    messages: list[dict] = [
        {"role": "user", "content": f"{context}\n\n---\n\n# Task\n{instructions}"}
    ]
    kwargs: dict[str, Any] = dict(
        model=MODEL,
        max_tokens=64000,
        system=prompts.SYSTEM,
        thinking={"type": "adaptive"},
        output_config=output_config,
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    if tools:
        kwargs["tools"] = tools

    text_parts: list[str] = []
    try:
        for _ in range(max_continuations + 1):
            with client().beta.messages.stream(messages=messages, **kwargs) as stream:
                msg = stream.get_final_message()
            if msg.stop_reason == "refusal":
                raise ClaudeError("Claude declined this request (refusal). Try rephrasing the brief.")
            text_parts.extend(b.text for b in msg.content if b.type == "text")
            if msg.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": msg.content})
                continue
            if msg.stop_reason == "max_tokens":
                raise ClaudeError("Output hit max_tokens; try a shorter target duration.")
            break
    except TypeError as e:
        # Raised by the SDK before sending when no credential is configured.
        if "authentication" not in str(e).lower():
            raise
        raise ClaudeError(
            "No Anthropic credentials found. Set ANTHROPIC_API_KEY in .env (then restart) or run `ant auth login`."
        ) from e
    except anthropic.AuthenticationError as e:
        raise ClaudeError(
            "Anthropic authentication failed. Set ANTHROPIC_API_KEY or run `ant auth login`."
        ) from e
    except anthropic.RateLimitError as e:
        raise ClaudeError("Rate limited by the Anthropic API; wait and retry.") from e
    except anthropic.APIStatusError as e:
        raise ClaudeError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ClaudeError(f"Could not reach the Anthropic API: {e}") from e

    return "".join(text_parts) if schema is not None else "\n".join(text_parts)


def _json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ClaudeError(f"Claude returned invalid JSON: {e}") from e


# --------------------------------------------------------------------------- context


def build_context(p: Project, *, include: tuple[str, ...]) -> str:
    b = p.brief
    out = [
        "# Brief",
        f"Title: {b.title}",
        f"Logline: {b.logline}",
        f"Concept: {b.concept}",
        f"Tone: {b.tone}",
        f"Visual style: {b.visual_style}",
        f"Music style: {b.music_style}",
        f"Target duration: {b.target_duration_s}s",
        f"Aspect ratio: {b.aspect_ratio}",
        f"Notes: {b.notes}",
    ]
    if "research" in include and p.research.strip():
        out += ["", "# Research dossier", p.research]
    if "lyrics" in include and p.lyrics.sections:
        out += ["", "# Song", f"Suno style: {p.lyrics.suno_style}"]
        if p.song.duration_s:
            out.append(f"Song duration: {p.song.duration_s:.2f}s")
        for s in p.lyrics.sections:
            timing = ""
            if s.start_s is not None and s.end_s is not None:
                timing = f" ({s.start_s:.2f}s - {s.end_s:.2f}s)"
            out.append(f"\n[{s.name}]{timing}")
            out.extend(s.lines)
    if "shots" in include and p.shots:
        out += ["", "# Shot list"]
        for s in p.shots:
            out.append(
                f"- {s.id} {s.start_s:.1f}-{s.end_s:.1f}s [{s.kind.value}] {s.section}: "
                f"{s.description}" + (f' | lyric: "{s.lyric}"' if s.lyric else "")
            )
    return "\n".join(out)


# --------------------------------------------------------------------------- stages


def run_research(p: Project, *, web_search: bool = True) -> str:
    tools = (
        [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]
        if web_search
        else None
    )
    return _call(prompts.RESEARCH, build_context(p, include=()), tools=tools).strip()


def run_lyrics(p: Project) -> Lyrics:
    data = _json(_call(prompts.LYRICS, build_context(p, include=("research",)), schema=prompts.LYRICS_SCHEMA))
    return Lyrics(
        suno_title=data["suno_title"],
        suno_style=data["suno_style"],
        sections=[LyricSection(name=s["name"], lines=s["lines"]) for s in data["sections"]],
    )


def _shot_from(d: dict, shot_id: str) -> Shot:
    return Shot(
        id=shot_id,
        section=d["section"],
        start_s=float(d["start_s"]),
        duration_s=float(d["duration_s"]),
        kind=ShotKind(d["kind"]),
        description=d["description"],
        camera=d["camera"],
        lyric=d["lyric"],
        midjourney_prompt=d["midjourney_prompt"],
        motion_prompt=d["motion_prompt"],
        on_screen_text=d["on_screen_text"],
    )


def normalize_timeline(shots: list[Shot], total: float | None) -> list[Shot]:
    """Make shots contiguous from 0, clamp motion shots to Seedance's 4-15s, and
    stretch the final shot to the song end."""
    shots = sorted(shots, key=lambda s: s.start_s)
    t = 0.0
    for s in shots:
        s.start_s = round(t, 3)
        s.duration_s = max(0.5, s.duration_s)
        if s.kind == ShotKind.motion:
            s.duration_s = min(15.0, max(4.0, s.duration_s))
        t += s.duration_s
    if total and shots:
        last = shots[-1]
        last.duration_s = max(0.5, round(total - last.start_s, 3))
    return shots


def run_storyboard(p: Project) -> list[Shot]:
    ctx = build_context(p, include=("research", "lyrics"))
    total = p.song.duration_s or float(p.brief.target_duration_s)
    ctx += f"\n\nThe shot list must cover exactly 0s to {total:.2f}s."
    data = _json(_call(prompts.STORYBOARD, ctx, schema=prompts.STORYBOARD_SCHEMA))
    new = [_shot_from(d, f"s{i + 1:03d}") for i, d in enumerate(data["shots"])]
    return normalize_timeline(new, total)


def run_sound(p: Project) -> list[SoundCue]:
    ctx = build_context(p, include=("research", "lyrics", "shots"))
    data = _json(_call(prompts.SOUND, ctx, schema=prompts.SOUND_SCHEMA))
    return [
        SoundCue(id=f"c{i + 1:03d}", at_s=float(c["at_s"]), duration_s=float(c["duration_s"]), description=c["description"])
        for i, c in enumerate(sorted(data["cues"], key=lambda c: c["at_s"]))
    ]


def refine_shot(p: Project, shot: Shot, note: str) -> Shot:
    ctx = build_context(p, include=("research", "lyrics", "shots"))
    ctx += "\n\n# Shot to revise\n" + json.dumps(
        {k: (v.value if hasattr(v, "value") else v) for k, v in shot.model_dump().items() if k in prompts.SHOT_PROPS},
        indent=2,
    )
    ctx += f"\n\n# Director's note\n{note}"
    d = _json(_call(prompts.REFINE_SHOT, ctx, schema=prompts.SHOT_SCHEMA))
    revised = _shot_from(d, shot.id)
    # Keep assets and job state; only the creative fields change.
    for keep in ("image", "video", "job_id", "job_status", "job_error", "ken_burns"):
        setattr(revised, keep, getattr(shot, keep))
    return revised
