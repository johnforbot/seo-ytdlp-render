"""
FastAPI wrapper that proxies YouTube downloads through a self-hosted cobalt instance.

Endpoints
---------
GET  /health                         -> "ok"
POST /formats  {"url": "..."}        -> JSON with available quality options
GET  /download?url=...&format_id=... -> streams the file

Auth: every request must send  X-API-Key: <SECRET_KEY>
Env vars required:
  SECRET_KEY   - shared secret between this service and your frontend
  COBALT_URL   - base URL of your self-hosted cobalt (e.g. https://cobalt-xyz.onrender.com)
"""

import os
import requests
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

SECRET_KEY = os.environ["SECRET_KEY"]
COBALT_URL = os.environ["COBALT_URL"].rstrip("/")

app = FastAPI()


def check_auth(x_api_key: str | None):
    if x_api_key != SECRET_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")


def cobalt_request(payload: dict) -> dict:
    """POST to cobalt and return parsed JSON, or raise 502."""
    try:
        r = requests.post(
            f"{COBALT_URL}/",
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=30,
        )
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"cobalt unreachable: {e}")

    if data.get("status") == "error":
        raise HTTPException(status_code=502, detail=data.get("error", {}).get("code", "cobalt error"))
    return data


class FormatsBody(BaseModel):
    url: str


@app.get("/health")
def health():
    return "ok"


@app.post("/formats")
def formats(body: FormatsBody, x_api_key: str = Header(default=None)):
    """
    Cobalt doesn't expose a raw format list like yt-dlp, but it accepts quality hints.
    We return a fixed menu of qualities the frontend can pick from.
    """
    check_auth(x_api_key)

    # Quick probe: ask cobalt for the default best to make sure the URL is valid
    probe = cobalt_request({"url": body.url, "videoQuality": "720"})
    title = probe.get("filename", "video").rsplit(".", 1)[0]

    qualities = [
        {"format_id": "2160", "label": "4K (2160p) — video+audio"},
        {"format_id": "1440", "label": "2K (1440p) — video+audio"},
        {"format_id": "1080", "label": "1080p — video+audio"},
        {"format_id": "720",  "label": "720p — video+audio"},
        {"format_id": "480",  "label": "480p — video+audio"},
        {"format_id": "360",  "label": "360p — video+audio"},
        {"format_id": "audio","label": "Audio only (mp3)"},
    ]
    return JSONResponse({"title": title, "formats": qualities})


@app.get("/download")
def download(
    url: str = Query(...),
    format_id: str = Query("720"),
    x_api_key: str = Header(default=None),
):
    check_auth(x_api_key)

    if format_id == "audio":
        payload = {"url": url, "downloadMode": "audio", "audioFormat": "mp3"}
    else:
        payload = {"url": url, "videoQuality": format_id, "filenameStyle": "basic"}

    data = cobalt_request(payload)

    # cobalt returns either { status: "tunnel"|"redirect", url: "..." } or { status: "picker", picker: [...] }
    dl_url = data.get("url")
    if not dl_url:
        # picker case (rare on YouTube) — take the first item
        picker = data.get("picker") or []
        if picker:
            dl_url = picker[0].get("url")
    if not dl_url:
        raise HTTPException(status_code=502, detail="cobalt returned no download url")

    filename = data.get("filename", "download")

    # Stream cobalt's file back to the client
    upstream = requests.get(dl_url, stream=True, timeout=60)
    if upstream.status_code != 200:
        raise HTTPException(status_code=502, detail=f"upstream {upstream.status_code}")

    return StreamingResponse(
        upstream.iter_content(chunk_size=64 * 1024),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
