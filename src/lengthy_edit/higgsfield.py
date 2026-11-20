"""Seedance motion shots through Higgsfield's API (https://docs.higgsfield.ai).

Flow per shot:
  1. upload the shot's still via a presigned URL  (POST /files/generate-upload-url, then PUT)
  2. submit  POST /bytedance/seedance-2.0/image-to-video  (or text-to-video if no still)
  3. poll the returned ``status_url`` until completed / failed / nsfw / canceled
  4. download ``video.url`` into the project (Higgsfield deletes outputs after ~7 days)

Credentials: HF_API_KEY_ID and HF_API_KEY_SECRET (create them at console.higgsfield.ai).
"""

from __future__ import annotations

import mimetypes
import os
import uuid
from pathlib import Path

import httpx

BASE_URL = os.environ.get("HIGGSFIELD_BASE_URL", "https://api.higgsfield.ai")
SEEDANCE_MODEL = os.environ.get("HIGGSFIELD_SEEDANCE_MODEL", "bytedance/seedance-2.0")
RESOLUTION = os.environ.get("HIGGSFIELD_RESOLUTION", "1080p")

TERMINAL = {"completed", "failed", "nsfw", "canceled"}


class HiggsfieldError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.environ.get("HF_API_KEY_ID") and os.environ.get("HF_API_KEY_SECRET"))


def _headers() -> dict[str, str]:
    if not configured():
        raise HiggsfieldError("Set HF_API_KEY_ID and HF_API_KEY_SECRET to use Seedance via Higgsfield.")
    return {
        "Authorization": f"Key {os.environ['HF_API_KEY_ID']}:{os.environ['HF_API_KEY_SECRET']}",
        "Content-Type": "application/json",
    }


def _check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        raise HiggsfieldError(f"Higgsfield {r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else {}


def upload(path: Path) -> str:
    """Upload a local file and return its public URL."""
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with httpx.Client(timeout=60) as c:
        meta = _check(
            c.post(f"{BASE_URL}/files/generate-upload-url", headers=_headers(), json={"content_type": content_type})
        )
        # Storage URL: send only the headers Higgsfield returned, never our API key.
        put_headers = dict(meta.get("upload_headers") or {})
        put_headers.setdefault("Content-Type", content_type)
        r = c.put(meta["upload_url"], content=path.read_bytes(), headers=put_headers, timeout=300)
        if r.status_code >= 400:
            raise HiggsfieldError(f"upload PUT failed {r.status_code}: {r.text[:300]}")
    return meta["public_url"]


def submit_motion(
    *,
    prompt: str,
    duration_s: float,
    image_url: str | None,
    aspect_ratio: str = "16:9",
    generate_audio: bool = False,
) -> dict:
    """Submit a Seedance job. Returns Higgsfield's queued response
    (``request_id``, ``status_url``, ``cancel_url``, ...)."""
    duration = int(min(15, max(4, round(duration_s))))
    body: dict = {"duration": duration, "resolution": RESOLUTION, "generate_audio": generate_audio}
    if prompt.strip():
        body["prompt"] = prompt.strip()
    if image_url:
        endpoint = f"{SEEDANCE_MODEL}/image-to-video"
        body["image_url"] = image_url  # image-to-video takes aspect from the image
    else:
        endpoint = f"{SEEDANCE_MODEL}/text-to-video"
        body["aspect_ratio"] = aspect_ratio
        if "prompt" not in body:
            raise HiggsfieldError("text-to-video needs a motion prompt or description")
    headers = _headers() | {"Idempotency-Key": str(uuid.uuid4())}
    with httpx.Client(timeout=60) as c:
        return _check(c.post(f"{BASE_URL}/{endpoint}", headers=headers, json=body))


def status(status_url: str) -> dict:
    with httpx.Client(timeout=30) as c:
        return _check(c.get(status_url, headers=_headers()))


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, timeout=300, follow_redirects=True) as r:
        if r.status_code >= 400:
            raise HiggsfieldError(f"download failed {r.status_code}")
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    tmp.replace(dest)
    return dest
