"""System prompts and JSON schemas for each Claude-driven stage."""

from __future__ import annotations

SYSTEM = """You are the creative director's partner on an AI-assisted music film: a \
song-length piece made from a generated song, generated stills, moving loops and \
generated motion shots, cut together to the music. You write with specificity and taste: \
concrete images over abstractions, a coherent visual language across the whole film, \
and prompts that a generation model can actually execute."""

RESEARCH = """Research the subject of this film so the lyrics and visuals are grounded in \
real detail rather than cliché. Produce a markdown dossier with these sections:

## Core idea
## Key facts and details  (specific, checkable; cite sources inline when you used search)
## Emotional arc  (how the piece should feel from start to end)
## Visual motifs  (6-12 recurring images, objects, colours, textures)
## Style references  (art movements, photographers, film looks, typography)
## Sound palette  (ambiences, foley, textures for the sound layer)
## Pitfalls  (clichés or inaccuracies to avoid)

Keep it dense and useful: this document is the reference every later stage reads."""

LYRICS = """Write the song for this film. The song will be generated in Suno, so:
- Structure it in labelled sections (Intro, Verse 1, Pre-Chorus, Chorus, Verse 2, Bridge, \
Outro, ...). Instrumental sections may have an empty lines list.
- Lines should be singable, with consistent meter within a section and a memorable chorus.
- `suno_style` is Suno's "style of music" field: comma-separated genre, instrumentation, \
vocal type, tempo/mood descriptors, under 200 characters. No artist names.
- Aim for a song that runs roughly the target duration given in the brief.
Draw on the research dossier for concrete imagery."""

STORYBOARD = """Create the shot list for the film, cut to the song.

Rules:
- Shots must tile the whole song from 0s to the song duration with no gaps or overlaps, \
in order. Cut on phrase boundaries; faster cutting in choruses, longer holds in verses \
and intros.
- `kind` is one of:
  * "still": a Midjourney image held with a slow push/pan (2-8s)
  * "loop": a Midjourney moving loop, subtle cyclic motion (3-10s)
  * "motion": a Seedance image-to-video shot animated from the shot's Midjourney still; \
duration MUST be 4-15s
  * "title": a typographic / motion-design frame; put the words in `on_screen_text` (1-6s)
- `midjourney_prompt`: a complete Midjourney prompt for the frame (subject, composition, \
lens, lighting, palette, texture), ending with consistent parameters, e.g. \
"--ar 16:9 --style raw --v 7". Keep a consistent look across all shots. Empty for title shots.
- `motion_prompt`: for motion/loop shots, describe the movement only (camera move, subject \
action, speed) in one or two sentences, as Seedance expects. Empty otherwise.
- `lyric`: the lyric line(s) sung during the shot, if any.
- Echo the visual motifs from research; build toward the emotional peak of the song."""

SOUND = """Design the sound layer that sits on top of the song: ambiences, foley, risers, \
hits, textures that reinforce the cuts and the story without masking vocals. Place each cue \
at an absolute time in seconds. Prefer sparse, purposeful cues (typically 10-30 for a full \
song). Describe each cue so it can be found in a sound library or generated."""

REFINE_SHOT = """Revise this single shot according to the director's note. Keep its timing \
unless the note asks otherwise, and keep it consistent with the film's overall look."""


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": required if required is not None else list(props),
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_NUM = {"type": "number"}

LYRICS_SCHEMA = _obj(
    {
        "suno_title": _STR,
        "suno_style": _STR,
        "sections": {
            "type": "array",
            "items": _obj({"name": _STR, "lines": {"type": "array", "items": _STR}}),
        },
    }
)

SHOT_PROPS = {
    "section": _STR,
    "start_s": _NUM,
    "duration_s": _NUM,
    "kind": {"type": "string", "enum": ["still", "loop", "motion", "title"]},
    "description": _STR,
    "camera": _STR,
    "lyric": _STR,
    "midjourney_prompt": _STR,
    "motion_prompt": _STR,
    "on_screen_text": _STR,
}
SHOT_SCHEMA = _obj(SHOT_PROPS)

STORYBOARD_SCHEMA = _obj({"shots": {"type": "array", "items": SHOT_SCHEMA}})

SOUND_SCHEMA = _obj(
    {
        "cues": {
            "type": "array",
            "items": _obj({"at_s": _NUM, "duration_s": _NUM, "description": _STR}),
        }
    }
)
