# Tubekit yt-dlp Render service

This folder is the complete repo you can upload to GitHub for Render.

## Files

- `Dockerfile` — installs Python, ffmpeg, and the app.
- `main.py` — FastAPI wrapper around `yt-dlp`.
- `requirements.txt` — Python dependencies.
- `render.yaml` — optional Render blueprint.

## Render settings

- Environment: Docker
- Root directory: leave blank if this folder is the repo root, or set `services/ytdlp` if you upload the full app repo.
- Health check path: `/health`
- Environment variable: `YTDLP_SERVICE_TOKEN` = the same private token you save in Lovable.

After Render gives you a URL, save these two secrets in Lovable:

- `YTDLP_SERVICE_URL` = your Render URL, for example `https://your-service.onrender.com`
- `YTDLP_SERVICE_TOKEN` = the same token you put in Render