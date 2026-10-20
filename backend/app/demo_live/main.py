"""Internal-only API. Authentication belongs to the frontend BFF."""
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .runtime import Runtime, SOURCES


def create_app(runtime=None):
    runtime = runtime if runtime is not None else Runtime()

    @asynccontextmanager
    async def lifespan(app):
        runtime.start()
        try:
            yield
        finally:
            await run_in_threadpool(runtime.stop)

    app = FastAPI(title="Isolated live inference demo", lifespan=lifespan)
    app.state.runtime = runtime

    def validate_camera(camera_id):
        if camera_id not in SOURCES:
            raise HTTPException(404, "Unknown demo camera", headers={"Cache-Control": "no-store"})

    @app.get("/health")
    def health():
        return JSONResponse({
            "status": "error" if runtime.model_error else "ready" if runtime.models_ready else "loading",
            "models_ready": runtime.models_ready,
            "model_error": runtime.model_error,
            "cameras": {str(key): runtime.status(key) for key in SOURCES},
        }, headers={"Cache-Control": "no-store"})

    @app.get("/cameras/{camera_id}/status")
    def status(camera_id: int):
        validate_camera(camera_id)
        return JSONResponse(runtime.status(camera_id), headers={"Cache-Control": "no-store"})

    @app.get("/cameras/{camera_id}/frame")
    def frame(camera_id: int, mode: Literal["apd", "vehicle"] = "apd"):
        validate_camera(camera_id)
        result = runtime.frame(camera_id, mode)
        if result is None:
            raise HTTPException(503, "No fresh inference frame available", headers={"Cache-Control": "no-store", "Retry-After": "2"})
        data, sequence, timestamp = result
        return Response(data, media_type="image/jpeg", headers={
            "Cache-Control": "no-store", "X-Frame-Sequence": str(sequence),
            "X-Frame-Timestamp": str(timestamp),
        })

    return app


app = create_app()
