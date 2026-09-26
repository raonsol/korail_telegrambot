"""PWA 정적 파일 서빙 (/app/*, SPA fallback)"""

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi import Response
from fastapi.responses import FileResponse, RedirectResponse

logger = logging.getLogger(__name__)

SRC_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DIST_CANDIDATES = (
    SRC_DIR / "webapp_dist",  # Docker 이미지 (멀티 스테이지 빌드 결과)
    SRC_DIR.parent / "webapp" / "dist",  # 로컬 (make webapp-build)
)

# 파일명에 해시가 붙은 빌드 산출물
IMMUTABLE_PREFIX = "assets/"
NO_CACHE_FILES = {"sw.js", "index.html", "manifest.webmanifest", "registerSW.js"}


def resolve_dist_dir(configured: str = "") -> Optional[Path]:
    candidates = [Path(configured)] if configured else list(DEFAULT_DIST_CANDIDATES)
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate.resolve()
    return None


def mount_webapp(app: FastAPI, dist_dir: Optional[Path]) -> None:
    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/app/")

    @app.get("/app", include_in_schema=False)
    async def app_root():
        return RedirectResponse("/app/")

    if dist_dir is None:
        logger.warning(
            "Webapp build not found (run 'make webapp-build'); /app will return 404"
        )
        return

    index = dist_dir / "index.html"

    @app.get("/app/{path:path}", include_in_schema=False)
    async def webapp(path: str):
        target = (dist_dir / path).resolve()
        if (
            path
            and target.is_file()
            and os.path.commonpath([target, dist_dir]) == str(dist_dir)
        ):
            if path.startswith(IMMUTABLE_PREFIX):
                headers = {"Cache-Control": "public, max-age=31536000, immutable"}
            elif target.name in NO_CACHE_FILES:
                headers = {"Cache-Control": "no-cache"}
            else:
                headers = {"Cache-Control": "public, max-age=3600"}
            return FileResponse(target, headers=headers)
        if Path(path).suffix:
            return Response(status_code=404)
        # 클라이언트 라우팅 경로는 index.html로
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
