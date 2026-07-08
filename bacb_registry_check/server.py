"""HTTP server for the standalone BACB registry checker."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from bacb_registry_check.checker import BacbCheckerConfig, BacbRegistryChecker
from bacb_registry_check.schemas import BacbCheckRequest, BacbCheckResponse


def config_from_env() -> BacbCheckerConfig:
    """Build checker config from environment variables."""

    return BacbCheckerConfig(
        cdp_url=os.environ.get("BACB_CDP_URL", "http://192.168.65.254:9222/"),
        screenshot_dir=Path(os.environ.get("BACB_SCREENSHOT_DIR", "/data/bacb-registry")),
        public_base_url=os.environ.get("BACB_PUBLIC_BASE_URL", "http://127.0.0.1:8765"),
    )


def create_app(config: BacbCheckerConfig | None = None) -> FastAPI:
    """Create the FastAPI app for BACB registry checks."""

    resolved_config = config or config_from_env()
    checker = BacbRegistryChecker(resolved_config)
    app = FastAPI(title="BACB Registry Check", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/check")
    async def check(request: BacbCheckRequest) -> BacbCheckResponse:
        return await checker.check(request)

    @app.get("/screenshots/{filename}")
    async def screenshot(filename: str) -> FileResponse:
        if "/" in filename or ".." in filename:
            raise HTTPException(status_code=404, detail="screenshot not found")
        path = resolved_config.screenshot_dir / filename
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="screenshot not found")
        return FileResponse(path, media_type="image/png", filename=filename)

    return app


def serve(host: str, port: int) -> None:
    """Start the BACB registry checker HTTP server."""

    uvicorn.run(create_app(), host=host, port=port)
