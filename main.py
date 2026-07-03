"""
FastAPI wrapper — cobalt.tools + Piped fallback (no yt-dlp, no cookies).

Endpoints
---------
GET  /                           -> status
GET  /health                     -> 200
POST /formats  {"url": "..."}    -> JSON metadata + formats
GET  /download?url=...&format_id=...  -> streams the file

Auth header: X-Auth-Token: <YTDLP_SERVICE_TOKEN>
"""
import os
import re
import json
import requests
from fastapi import FastAPI, HTTPException, Header, Query, Response
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI()
TOKEN = os.environ.get("YTDLP_SERVICE_TOKEN", "")

# Community cobalt instances (rotate if one is down)
COBALT_INSTANCES = [
    "https://cobalt-api.ayo.tf",
    "https://api.cobalt.tools",
    "https://co.eepy.today",
]

# Public Piped API instances (fallback for metadata + formats)
PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.reallyaweso.me",
    "https://api.piped.private.coffee",
]

YT_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})")


def check_auth(header_token):
    if not TOKEN:
        raise HTTPException(500, "YTDLP_SERVICE_TOKEN not set")
    if header_token != TOKEN:
        raise HTTPException(401, "Bad token")


def extract_video_id(url: str) -> str | None:
    m = YT_ID_RE.search(url)
    return m.group(1) if m else None


def piped_get_info(video_id: str):
    """Try each Piped instance until one works. Returns raw JSON or None."""
    for base in PIPED_INSTANCES:
        try:
            r = requests.get(f"{base}/streams/{video_id}", timeout=15)
            if r.ok:
                return r.json(), base
        except Exception:
            continue
    return None, None


@app.get("/")
def root():
    return {"status": "working", "service": "cobalt + piped API"}


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return Response(status_code=200)


class FormatsBody(BaseModel):
    url: str


@app.post("/formats")
def formats(body: FormatsBody, x_auth_token: str | None = Header(default=None)):
    check_auth(x_auth_token)
    vid = extract_video_id(body.url)
    if not vid:
        raise HTTPException(400, "Could not parse YouTube video ID from URL")

    data, _ = piped_get_info(vid)
    if not data:
        raise HTTPException(502, "All Piped instances failed. Try again.")

    formats_out = []

    # Video streams (video-only, mostly)
    for s in data.get("videoStreams", []) or []:
        formats_out.append({
            "format_id": f"v:{s.get('itag')}",
            "ext": s.get("format", "").lower().replace("mpeg_4", "mp4") or "mp4",
            "resolution": s.get("quality"),
            "height": int(re.sub(r"\D", "", s.get("quality") or "0") or 0),
            "fps": s.get("fps"),
            "vcodec": s.get("codec"),
            "acodec": "none" if s.get("videoOnly") else "aac",
            "filesize": None,
            "note": "video only" if s.get("videoOnly") else "video + audio",
            "_url": s.get("url"),
        })

    # Audio streams
    for s in data.get("audioStreams", []) or []:
        formats_out.append({
            "format_id": f"a:{s.get('itag')}",
            "ext": s.get("format", "").lower().replace("m4a", "m4a") or "m4a",
            "resolution": "audio only",
            "height": 0,
            "fps": None,
            "vcodec": "none",
            "acodec": s.get("codec") or "aac",
            "filesize": None,
            "note": f"{s.get('bitrate', '')} audio",
            "_url": s.get("url"),
        })

    return JSONResponse({
        "id": vid,
        "title": data.get("title"),
        "thumbnail": data.get("thumbnailUrl"),
        "duration": data.get("duration"),
        "uploader": data.get("uploader"),
        "formats": formats_out,
    })


def cobalt_download_url(url: str) -> str | None:
    """Ask cobalt for a direct download link. Returns URL or None."""
    for base in COBALT_INSTANCES:
        try:
            r = requests.post(
                f"{base}/",
                json={"url": url, "videoQuality": "1080", "filenameStyle": "basic"},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=25,
            )
            if not r.ok:
                continue
            j = r.json()
            status = j.get("status")
            if status in ("tunnel", "redirect") and j.get("url"):
                return j["url"]
        except Exception:
            continue
    return None


@app.get("/download")
def download(
    url: str = Query(...),
    format_id: str = Query("best"),
    x_auth_token: str | None = Header(default=None),
):
    check_auth(x_auth_token)

    direct_url = None

    # Path 1: format_id came from /formats — Piped gave us a direct googlevideo URL.
    # We need to re-resolve it because those URLs are per-request/short-lived.
    vid = extract_video_id(url)
    if vid and format_id and format_id != "best":
        data, _ = piped_get_info(vid)
        if data:
            wanted_itag = format_id.split(":", 1)[-1]
            for pool in (data.get("videoStreams") or [], data.get("audioStreams") or []):
                for s in pool:
                    if str(s.get("itag")) == wanted_itag:
                        direct_url = s.get("url")
                        break
                if direct_url:
                    break

    # Path 2: no match or format_id=best → use cobalt for a merged file
    if not direct_url:
        direct_url = cobalt_download_url(url)

    if not direct_url:
        raise HTTPException(502, "No provider returned a download URL")

    # Stream the file to the client
    upstream = requests.get(direct_url, stream=True, timeout=30)
    if not upstream.ok:
        raise HTTPException(502, f"Upstream fetch failed: {upstream.status_code}")

    def gen():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(gen(), media_type="application/octet-stream")
