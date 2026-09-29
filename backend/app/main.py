from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import ApiError, router
from app.db import init_db
from app.services.pipeline import CrossBidderPathLeak

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Tender Evaluator PoC", version="0.1.0", lifespan=lifespan)
app.include_router(router)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"success": False, "error": {"code": code, "message": message}})


@app.exception_handler(ApiError)
async def api_error(_: Request, exc: ApiError):
    return _error(exc.status, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    return _error(400, "VAL_001", "Validation failed: " + "; ".join(e.get("msg", "") for e in exc.errors()))


@app.exception_handler(CrossBidderPathLeak)
async def path_leak(_: Request, exc: CrossBidderPathLeak):
    logging.getLogger("security").critical("Blocked cross-bidder path: %s", exc)
    return _error(403, "SEC_001", "A security check blocked this action. Contact support.")
