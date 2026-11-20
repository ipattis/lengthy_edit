# lengthy-edit

A local web UI and Python package for producing **song-length AI music films**, modelled on
the workflow behind [*Working as Intended*](https://www.youtube.com/watch?v=Z6_ENp_fO_Y)
([reference](docs/workflow-reference.png)):

| Stage | Tool | In lengthy-edit |
|---|---|---|
| Directing | you | Brief, edits on every field, per-shot "refine with Claude" notes |
| Research | **Claude** (+ web search) | Generates a markdown dossier: facts, emotional arc, visual motifs, sound palette |
| Lyrics | **Claude** | Sectioned lyrics + Suno "style of music" prompt, one-click copy |
| Song | **Suno** (manual) | Upload the take you like; duration probed; section timings auto-estimated, then set by ear with "start = playhead" |
| Storyboard + prompts | **Claude** | Shot list tiled to the song: still / loop / motion / title shots, Midjourney prompts, motion prompts, lyrics per shot |
| Stills & loops | **Midjourney** (manual) | Copy prompts (or export `prompts.csv`), upload images/loops per shot |
| Motion shots | **Seedance via Higgsfield API** | One click (or "all missing") animates a shot's still with Seedance 2.0 image-to-video; polls and downloads automatically |
| Sound layer | **Claude** + your library | Timed cue sheet; upload a file per cue; mixed under the song |
| Motion-design frames | local | `title` shots render as typographic cards with fades |
| Cut | **ffmpeg** | Renders an animatic (placeholder cards for missing assets) at any time, then the final film. Segments are cached, so re-rendering a long edit only re-encodes changed shots |

Midjourney and Suno don't have official public APIs, so those steps are copy-prompt / upload-result.
Everything else is automated.

## Install

Requires Python ≥ 3.10 and `ffmpeg`/`ffprobe` on `PATH` (`brew install ffmpeg`).

```bash
git clone <this repo> && cd lengthy_edit
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env    # fill in keys
lengthy-edit serve      # opens http://127.0.0.1:8765
```

### Credentials

| Variable | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` (or `ant auth login`) | All Claude stages |
| `HF_API_KEY_ID`, `HF_API_KEY_SECRET` | Seedance motion shots ([console.higgsfield.ai](https://console.higgsfield.ai)) |

Optional: `LENGTHY_EDIT_MODEL` (default `claude-opus-5-5`), `LENGTHY_EDIT_EFFORT` (default `high`),
`HIGGSFIELD_SEEDANCE_MODEL` (default `bytedance/seedance-2.0`), `HIGGSFIELD_RESOLUTION` (default `1080p`),
`LENGTHY_EDIT_HOME` (project folder, default `./projects`).

`.env` in the working directory is loaded automatically; real environment variables win.

## Workflow

1. **Brief**: title, concept, tone, visual + music style, target length.
2. **Research**: generate, then edit. Every later stage reads it.
3. **Lyrics & Song**: generate lyrics, copy style + lyrics into Suno, upload the song,
   auto-time sections, then correct timings while listening.
4. **Storyboard & Assets**: generate the shot list. For each shot:
   - `still` / `loop`: generate in Midjourney from the prompt, upload the image or loop video.
   - `motion`: upload the Midjourney still, then click **Seedance** (or *Seedance all missing*).
   - `title`: edit the on-screen text; rendered locally.
   Use **Refine with Claude** for director's notes on a single shot, and **Fit to song** after manual timing edits.
5. **Sound layer**: generate cues, attach sound files, set gain.
6. **Render**: animatic first, final once every shot has an asset.

## CLI

```bash
lengthy-edit serve [--port 8765] [--home DIR] [--no-browser]
lengthy-edit new "My Film"
lengthy-edit list
lengthy-edit render my-film      # headless render
```

## Project layout on disk

```
projects/<slug>/
  project.json            # all text, timings, prompts, links to media
  song/  shots/<id>/  sound/
  renders/                # final mp4s + cached segments
```

`project.json` is plain, diffable JSON. Media stays out of git by default (`projects/` is ignored).

## Development

```bash
pytest                       # tests use synthetic media; no API calls
lengthy-edit serve --reload
```

Code map: `models.py` (data), `store.py` (persistence), `claude.py` + `prompts.py` (Claude stages),
`higgsfield.py` (Seedance), `render.py` (ffmpeg), `server.py` (API), `static/` (UI).
