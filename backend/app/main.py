"""SnapScan 本地后端入口：纯离线，手机连热点局域网访问。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from .core.config import UPLOAD_DIR, DATA_DIR
from .routers import health, scan, ocr, history, export_router

app = FastAPI(title="SnapScan 离线智能扫描仪", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
# 手机下载APK：把项目 releases/ 暴露为 /app（仅局域网演示用）
import pathlib as _pl
_RELEASES = str((_pl.Path(UPLOAD_DIR).parent / "releases"))
os.makedirs(_RELEASES, exist_ok=True)
app.mount("/app", StaticFiles(directory=_RELEASES, html=True), name="app")

app.include_router(health.router)
app.include_router(scan.router)
app.include_router(ocr.router)
app.include_router(history.router)
app.include_router(export_router.router)

@app.get("/")
def root():
    return {"name": "SnapScan", "offline": True, "docs": "/docs", "health": "/api/health", "apk": "/app/"}
