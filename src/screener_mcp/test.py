from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="DISHA AI - Screener MCP")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Serve css/js files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(
        STATIC_DIR / "index.html",
        media_type="text/html"
    )


@app.get("/healths")
async def health():
    return JSONResponse(
        {
            "status": "ok",
            "service": "screener-mcp",
            "auth": "disabled"
        }
    )


@app.get("/banner")
async def banner():
    for name in (
        "banner.png",
        "banner.jpg",
        "banner.jpeg",
        "banner.webp",
        "banner.gif",
    ):
        path = STATIC_DIR / name
        if path.exists():
            return FileResponse(path)

    return JSONResponse(
        {"error": "Banner not found"},
        status_code=404,
    )


@app.get("/favicon.ico")
async def favicon():
    path = STATIC_DIR / "favicon.ico"

    if path.exists():
        return FileResponse(
            path,
            media_type="image/x-icon"
        )

    return JSONResponse(
        {"error": "favicon not found"},
        status_code=404,
    )