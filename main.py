"""
Tiny FastAPI wrapper around yt-dlp.

Endpoints
---------
GET  /health                     -> "ok"
POST /formats  {"url": "..."}    -> JSON metadata + downloadable formats
GET  /download?url=...&format_id=137+140  -> streams the merged file

Auth: every request must send   X-Auth-Token: <YTDLP_SERVICE_TOKEN>
Set that env var on Render/Fly, and set the same value in your Lovable secret
YTDLP_SERVICE_TOKEN.
"""
import os
import subprocess
import json
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI()
TOKEN = os.environ.get("YTDLP_SERVICE_TOKEN", "")


def check_auth(header_token: str | None):
    if not TOKEN:
        raise HTTPException(500, "YTDLP_SERVICE_TOKEN is not set on the server.")
    if header_token != TOKEN:
        raise HTTPException(401, "Bad token")


class FormatsBody(BaseModel):
    url: str


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/formats")
def formats(body: FormatsBody, x_auth_token: str | None = Header(default=None)):
    check_auth(x_auth_token)
    try:
        out = subprocess.check_output(
            ["yt-dlp", "-J", "--no-warnings", body.url],
            stderr=subprocess.STDOUT,
            timeout=60,
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(400, f"yt-dlp failed: {e.output.decode()[:500]}")
    info = json.loads(out)
    return JSONResponse(
        {
            "id": info.get("id"),
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "uploader": info.get("uploader"),
            "formats": [
                {
                    "format_id": f.get("format_id"),
                    "ext": f.get("ext"),
                    "resolution": f.get("resolution"),
                    "height": f.get("height"),
                    "fps": f.get("fps"),
                    "vcodec": f.get("vcodec"),
                    "acodec": f.get("acodec"),
                    "filesize": f.get("filesize") or f.get("filesize_approx"),
                    "note": f.get("format_note"),
                }
                for f in (info.get("formats") or [])
            ],
        }
    )


@app.get("/download")
def download(
    url: str = Query(...),
    format_id: str = Query("best"),
    x_auth_token: str | None = Header(default=None),
):
    check_auth(x_auth_token)
    cmd = ["yt-dlp", "-f", format_id, "-o", "-", "--no-warnings", url]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def stream():
        assert proc.stdout is not None
        try:
            while True:
                chunk = proc.stdout.read(1024 * 64)
                if not chunk:
                    break
                yield chunk
        finally:
            proc.kill()

    return StreamingResponse(stream(), media_type="application/octet-stream")