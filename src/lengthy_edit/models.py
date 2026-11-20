"""Domain model for a lengthy-edit project.

A project moves through the stages of a typical long-form AI music-film edit:

    brief -> research -> lyrics (+ Suno style prompt) -> song (Suno, uploaded)
          -> storyboard (shots) -> assets (Midjourney stills/loops, Seedance motion)
          -> sound layer (cues) -> render (ffmpeg)

Everything is persisted as one ``project.json`` per project folder, so a project
is easy to diff, back up, or hand-edit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ShotKind(str, Enum):
    still = "still"  # Midjourney image, optional Ken Burns move
    loop = "loop"  # Midjourney moving loop (video), looped to fill duration
    motion = "motion"  # Seedance image-to-video (via Higgsfield) or text-to-video
    title = "title"  # Motion-design / typography frame rendered locally


class Brief(BaseModel):
    title: str = ""
    logline: str = ""
    concept: str = ""
    tone: str = ""
    visual_style: str = ""
    music_style: str = ""
    target_duration_s: int = 210
    aspect_ratio: str = "16:9"
    notes: str = ""


class LyricSection(BaseModel):
    name: str  # e.g. "Verse 1", "Chorus"
    lines: list[str] = Field(default_factory=list)
    # Timing inside the uploaded song, filled in during the Song stage.
    start_s: Optional[float] = None
    end_s: Optional[float] = None


class Lyrics(BaseModel):
    suno_style: str = ""  # Suno "style of music" field
    suno_title: str = ""
    sections: list[LyricSection] = Field(default_factory=list)

    def as_suno_text(self) -> str:
        parts = []
        for s in self.sections:
            parts.append(f"[{s.name}]")
            parts.extend(s.lines)
            parts.append("")
        return "\n".join(parts).strip()


class Song(BaseModel):
    file: Optional[str] = None  # relative to project dir
    duration_s: Optional[float] = None
    bpm: Optional[float] = None


class Shot(BaseModel):
    id: str
    section: str = ""
    start_s: float = 0.0
    duration_s: float = 4.0
    kind: ShotKind = ShotKind.still
    description: str = ""
    camera: str = ""
    lyric: str = ""
    midjourney_prompt: str = ""
    motion_prompt: str = ""  # Seedance / loop motion description
    on_screen_text: str = ""  # for title / motion-design frames
    # Asset files, relative to the project dir.
    image: Optional[str] = None
    video: Optional[str] = None
    # Higgsfield job tracking.
    job_id: Optional[str] = None
    job_status_url: Optional[str] = None
    job_status: Optional[str] = None
    job_error: Optional[str] = None
    ken_burns: bool = True

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration_s


class SoundCue(BaseModel):
    id: str
    at_s: float
    duration_s: float = 2.0
    description: str = ""
    file: Optional[str] = None
    gain_db: float = -6.0


class RenderSettings(BaseModel):
    width: int = 1920
    height: int = 1080
    fps: int = 24
    crf: int = 18
    song_gain_db: float = 0.0


class RenderRecord(BaseModel):
    file: str
    created_at: str = Field(default_factory=_now)
    animatic: bool = False


class Project(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    slug: str
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    brief: Brief = Field(default_factory=Brief)
    research: str = ""  # markdown
    lyrics: Lyrics = Field(default_factory=Lyrics)
    song: Song = Field(default_factory=Song)
    shots: list[Shot] = Field(default_factory=list)
    sound_cues: list[SoundCue] = Field(default_factory=list)
    render: RenderSettings = Field(default_factory=RenderSettings)
    renders: list[RenderRecord] = Field(default_factory=list)

    def touch(self) -> None:
        self.updated_at = _now()

    def shot(self, shot_id: str) -> Shot:
        for s in self.shots:
            if s.id == shot_id:
                return s
        raise KeyError(shot_id)

    def timeline_duration(self) -> float:
        return max((s.end_s for s in self.shots), default=0.0)

    def status(self) -> dict:
        """Coarse per-stage completeness for the UI."""
        shots = self.shots
        ready = [s for s in shots if s.kind == ShotKind.title or s.video or s.image]
        return {
            "brief": bool(self.brief.title and self.brief.concept),
            "research": bool(self.research.strip()),
            "lyrics": bool(self.lyrics.sections),
            "song": bool(self.song.file),
            "storyboard": bool(shots),
            "assets": f"{len(ready)}/{len(shots)}",
            "sound": len(self.sound_cues),
            "render": len(self.renders),
        }
