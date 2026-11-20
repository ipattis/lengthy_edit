"""Offline tests: storage, timeline maths, API flow, and a real ffmpeg render
using synthetic media. No Claude or Higgsfield calls are made."""

from __future__ import annotations

import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from lengthy_edit import claude, render, store
from lengthy_edit.models import Shot, ShotKind, SoundCue

needs_ffmpeg = pytest.mark.skipif(not render.ffmpeg_available(), reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("LENGTHY_EDIT_HOME", str(tmp_path / "ws"))
    return tmp_path / "ws"


@pytest.fixture
def client():
    from lengthy_edit.server import app

    return TestClient(app)


def test_create_and_slug_collision():
    a = store.create("Working as Intended!")
    b = store.create("Working as Intended!")
    assert a.slug == "working-as-intended"
    assert b.slug == "working-as-intended-2"
    assert store.load(a.slug).brief.title == "Working as Intended!"


def test_safe_path_blocks_traversal():
    p = store.create("x")
    with pytest.raises(ValueError):
        store.safe_path(p.slug, "../../etc/passwd")
    with pytest.raises(ValueError):
        store.project_dir("../evil")


def test_normalize_timeline_tiles_and_clamps():
    shots = [
        Shot(id="a", start_s=0, duration_s=3, kind=ShotKind.still),
        Shot(id="b", start_s=5, duration_s=2, kind=ShotKind.motion),  # too short for Seedance
        Shot(id="c", start_s=9, duration_s=30, kind=ShotKind.motion),  # too long
        Shot(id="d", start_s=40, duration_s=1, kind=ShotKind.title),
    ]
    out = claude.normalize_timeline(shots, total=60)
    assert [s.start_s for s in out] == [0, 3, 7, 22]
    assert out[1].duration_s == 4 and out[2].duration_s == 15
    assert out[-1].end_s == pytest.approx(60)


def test_api_flow(client):
    r = client.post("/api/projects", json={"title": "Test Film"})
    slug = r.json()["slug"]
    brief = r.json()["brief"] | {"concept": "a film about tests"}
    assert client.put(f"/api/projects/{slug}/brief", json=brief).status_code == 200

    lyrics = {"suno_title": "T", "suno_style": "synth", "sections": [
        {"name": "Verse", "lines": ["a", "b", "c", "d"]}, {"name": "Chorus", "lines": ["e", "f"]}]}
    client.put(f"/api/projects/{slug}/lyrics", json=lyrics)
    p = client.post(f"/api/projects/{slug}/lyrics/auto-time").json()
    secs = p["lyrics"]["sections"]
    assert secs[0]["start_s"] == 0 and secs[-1]["end_s"] == pytest.approx(210, abs=0.05)

    p = client.post(f"/api/projects/{slug}/shots", json={}).json()
    p = client.post(f"/api/projects/{slug}/shots", json={"after": "s001", "kind": "title"}).json()
    assert [s["id"] for s in p["shots"]] == ["s001", "s002"]
    p = client.patch(f"/api/projects/{slug}/shots/s002", json={"on_screen_text": "HELLO", "duration_s": 2}).json()
    assert p["shots"][1]["on_screen_text"] == "HELLO"
    assert client.patch(f"/api/projects/{slug}/shots/s002", json={"video": "/etc/passwd"}).status_code == 400
    assert "[Chorus]" in client.get(f"/api/projects/{slug}/export/suno.txt").text
    assert "s001" in client.get(f"/api/projects/{slug}/export/prompts.csv").text
    assert client.post(f"/api/projects/{slug}/upload?target=song", files={"file": ("x.exe", b"x")}).status_code == 400
    assert client.post(f"/api/projects/{slug}/generate/nope").status_code == 404


def _synth_media(d):
    d.mkdir(parents=True, exist_ok=True)
    song, img, vid, sfx = d / "song.wav", d / "still.png", d / "loop.mp4", d / "hit.wav"
    run = lambda *a: subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *a], check=True)
    run("-f", "lavfi", "-i", "sine=frequency=220:duration=7", str(song))
    run("-f", "lavfi", "-i", "sine=frequency=880:duration=0.5", str(sfx))
    run("-f", "lavfi", "-i", "testsrc2=size=800x600:rate=24", "-frames:v", "1", str(img))
    run("-f", "lavfi", "-i", "testsrc=size=640x360:rate=24:duration=1", "-pix_fmt", "yuv420p", str(vid))
    return song, img, vid, sfx


@needs_ffmpeg
def test_render_end_to_end(tmp_path):
    p = store.create("Render Me")
    pdir = store.project_dir(p.slug)
    song, img, vid, sfx = _synth_media(pdir / "media")
    p.song.file, p.song.duration_s = "media/song.wav", 7.0
    p.render.width, p.render.height, p.render.fps = 320, 180, 12
    p.shots = claude.normalize_timeline([
        Shot(id="s001", duration_s=2, kind=ShotKind.still, image="media/still.png"),
        Shot(id="s002", duration_s=2, kind=ShotKind.loop, video="media/loop.mp4"),  # 1s clip looped to 2s
        Shot(id="s003", duration_s=1.5, kind=ShotKind.title, on_screen_text="WORKING AS INTENDED"),
        Shot(id="s004", duration_s=1.5, kind=ShotKind.motion, description="missing asset"),
    ], total=7.0)
    p.sound_cues = [SoundCue(id="c001", at_s=2.0, duration_s=0.5, file="media/hit.wav")]
    store.save(p)

    rec = render.render_project(store.load(p.slug))
    out = pdir / rec.file
    assert out.is_file() and rec.animatic  # s004 has no asset -> placeholder
    assert render.probe_duration(out) == pytest.approx(7.0, abs=0.15)
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
                             capture_output=True, text=True).stdout.split()
    assert sorted(streams) == ["audio", "video"]

    # Second render reuses cached segments.
    segs_before = {f.name for f in (pdir / "renders" / "segments").glob("*.mp4")}
    render.render_project(store.load(p.slug))
    assert {f.name for f in (pdir / "renders" / "segments").glob("*.mp4")} == segs_before


def test_storyboard_parsing(monkeypatch):
    import json

    def fake_call(instructions, context, **kw):
        assert "0s to 20.00s" in context
        shot = {"section": "Intro", "kind": "motion", "description": "d", "camera": "", "lyric": "",
                "midjourney_prompt": "mj --ar 16:9", "motion_prompt": "slow push", "on_screen_text": ""}
        return json.dumps({"shots": [shot | {"start_s": 0, "duration_s": 2}, shot | {"start_s": 2, "duration_s": 8, "kind": "still"}]})

    monkeypatch.setattr(claude, "_call", fake_call)
    p = store.create("Parse")
    p.song.duration_s = 20.0
    shots = claude.run_storyboard(p)
    assert [s.id for s in shots] == ["s001", "s002"]
    assert shots[0].duration_s == 4  # motion clamped to Seedance minimum
    assert shots[1].start_s == 4 and shots[1].end_s == 20
