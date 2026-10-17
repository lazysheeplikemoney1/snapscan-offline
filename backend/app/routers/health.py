from fastapi import APIRouter
from fastapi.responses import JSONResponse
from ..services.ocr_engine import get_engine, OCRUnavailableError

router = APIRouter(prefix="/api", tags=["health"])
@router.get("/health")
def health():
    try:
        engine = get_engine()
    except OCRUnavailableError as exc:
        return JSONResponse(status_code=503, content={
            "status": "degraded", "offline": True, "version": "0.1.0",
            "ocr": {"ready": False, "error": str(exc)},
        })
    return {"status": "ok", "offline": True, "version": "0.1.0",
            "ocr": {"ready": True, "engine": engine.name, "model": engine.model_info}}
