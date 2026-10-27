from __future__ import annotations

import hmac
import ipaddress
import os
from pathlib import Path
import secrets
from threading import RLock

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from .models import Edition, Failure, Ingest, ReadRequest, Rebuild
from .model_settings import ModelSettingsError, ModelSettingsStore, UpdateModelSettings, configured_directory, model_catalog
from .preferences import UpdatePreferences
from .store import Conflict, Store

PREFIX = "/briefing/v1"
MANAGE_PREFIX = PREFIX + "/manage"
SESSION_COOKIE = "briefing_manage_session"
SESSION_SECONDS = 8 * 60 * 60


class SessionLogin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    token: str = Field(min_length=24, max_length=512)


def create_app(data_dir: Path | None = None, tokens: dict | None = None, clock=None) -> FastAPI:
    tokens = dict(tokens) if tokens is not None else {
        role: os.environ.get(f"BRIEFING_{role.upper()}_TOKEN", "") for role in ("ingest", "worker", "read", "manage")}
    original_tokens = [tokens.get(role, "") for role in ("ingest", "worker", "read")]
    if any(not isinstance(value, str) or len(value) < 24 for value in original_tokens) or len(set(original_tokens)) != 3:
        raise RuntimeError("Three distinct BRIEFING_*_TOKEN values of at least 24 characters are required")
    manage_token = tokens.get("manage", "")
    if manage_token and (not isinstance(manage_token, str) or len(manage_token) < 24 or manage_token in original_tokens):
        raise RuntimeError("BRIEFING_MANAGE_TOKEN must be distinct and contain at least 24 characters")
    directory = data_dir or Path(os.environ.get("BRIEFING_DATA_DIR", "runtime/cloud"))
    store = Store(directory, **({"clock": clock} if clock else {}))
    app = FastAPI(title="Private Daily Briefing", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    pi_settings = ModelSettingsStore(configured_directory())
    sessions: dict[str, float] = {}
    session_lock = RLock()
    static_directory = Path(__file__).resolve().parent / "management"

    def require(role: str):
        def check(authorization: str | None = Header(default=None)):
            expected = "Bearer " + tokens[role]
            if authorization is None or not hmac.compare_digest(authorization.encode(), expected.encode()):
                raise HTTPException(status_code=401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"})
        return check

    def manage_available():
        if not manage_token:
            raise HTTPException(503, detail="management_disabled")

    def manage_transport(request: Request):
        host = request.url.hostname or ""
        loopback = host == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if request.url.scheme != "https" and not (request.url.scheme == "http" and loopback):
            raise HTTPException(403, detail="https_required")

    def check_origin(request: Request, required: bool):
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if (required and origin is None) or (origin is not None and origin != expected):
            raise HTTPException(403, detail="origin_not_allowed")

    def require_manage(request: Request):
        manage_available()
        manage_transport(request)
        authorization = request.headers.get("authorization")
        if authorization is not None:
            check_origin(request, required=False)
            if not hmac.compare_digest(authorization.encode(), ("Bearer " + manage_token).encode()):
                raise HTTPException(401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"})
            return
        session_id = request.cookies.get(SESSION_COOKIE, "")
        with session_lock:
            if not session_id or sessions.get(session_id, 0) <= store.clock().timestamp():
                sessions.pop(session_id, None)
                raise HTTPException(401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"})
        check_origin(request, required=request.method not in ("GET", "HEAD", "OPTIONS"))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic errors can contain full rejected email text; never echo inputs.
        return JSONResponse(status_code=422, content={"detail": "invalid_request", "fields": [".".join(map(str, error["loc"])) for error in exc.errors()]})

    @app.exception_handler(Conflict)
    async def conflict_error(request: Request, exc: Conflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ModelSettingsError)
    async def model_settings_error(request: Request, exc: ModelSettingsError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.code})

    @app.middleware("http")
    async def private_headers(request: Request, call_next):
        length = request.headers.get("content-length", "0")
        if not length.isdigit() or int(length) > 4_000_000:
            response = JSONResponse(status_code=413, content={"detail": "request_too_large"})
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path == MANAGE_PREFIX or request.url.path.startswith(MANAGE_PREFIX + "/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                "img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get(PREFIX + "/health")
    def health():
        return {"status": "ok", "service": "daily-briefing"}

    @app.post(MANAGE_PREFIX + "/session")
    def login(payload: SessionLogin, request: Request):
        manage_available()
        manage_transport(request)
        check_origin(request, required=True)
        if not hmac.compare_digest(payload.token.encode(), manage_token.encode()):
            raise HTTPException(401, detail="unauthorized")
        now = store.clock().timestamp()
        session_id = secrets.token_urlsafe(32)
        with session_lock:
            for expired in [key for key, expires in sessions.items() if expires <= now]:
                sessions.pop(expired)
            if len(sessions) >= 128:
                sessions.pop(min(sessions, key=sessions.get))
            sessions[session_id] = now + SESSION_SECONDS
        response = JSONResponse({"status": "authenticated", "expires_in": SESSION_SECONDS})
        response.set_cookie(SESSION_COOKIE, session_id, max_age=SESSION_SECONDS, path=MANAGE_PREFIX,
                            httponly=True, secure=request.url.scheme == "https", samesite="strict")
        return response

    @app.delete(MANAGE_PREFIX + "/session", dependencies=[Depends(require_manage)])
    def logout(request: Request):
        with session_lock:
            sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        response = JSONResponse({"status": "logged_out"})
        response.delete_cookie(SESSION_COOKIE, path=MANAGE_PREFIX, httponly=True,
                               secure=request.url.scheme == "https", samesite="strict")
        return response

    @app.get(MANAGE_PREFIX + "/preferences", dependencies=[Depends(require_manage)])
    def get_preferences():
        return store.preferences()

    @app.put(MANAGE_PREFIX + "/preferences", dependencies=[Depends(require_manage)])
    def put_preferences(payload: UpdatePreferences):
        return store.update_preferences(payload)

    @app.get(MANAGE_PREFIX + "/status", dependencies=[Depends(require_manage)])
    def generation_status():
        return store.generation_status()

    @app.get(MANAGE_PREFIX + "/model-catalog", dependencies=[Depends(require_manage)])
    def get_model_catalog():
        return model_catalog()

    @app.get(MANAGE_PREFIX + "/model-settings", dependencies=[Depends(require_manage)])
    def get_model_settings():
        return pi_settings.public()

    @app.put(MANAGE_PREFIX + "/model-settings", dependencies=[Depends(require_manage)])
    def put_model_settings(payload: UpdateModelSettings):
        return pi_settings.update(payload)

    @app.get(MANAGE_PREFIX)
    def management_redirect():
        return RedirectResponse(MANAGE_PREFIX + "/", status_code=307)

    @app.get(MANAGE_PREFIX + "/")
    def management_page():
        index = static_directory / "index.html"
        if not index.is_file():
            raise HTTPException(404, detail="management_page_unavailable")
        return FileResponse(index, media_type="text/html")

    @app.get(MANAGE_PREFIX + "/{asset}")
    def management_asset(asset: str):
        media = {"app.js": "text/javascript", "styles.css": "text/css"}
        if asset not in media or not (static_directory / asset).is_file():
            raise HTTPException(404, detail="not_found")
        return FileResponse(static_directory / asset, media_type=media[asset])

    @app.post(PREFIX + "/ingest", dependencies=[Depends(require("ingest"))])
    def ingest(payload: Ingest):
        return store.ingest(payload)

    @app.get(PREFIX + "/jobs/next", dependencies=[Depends(require("worker"))])
    def next_job():
        return store.next_job()

    @app.post(PREFIX + "/jobs/failure", dependencies=[Depends(require("worker"))])
    def failure(payload: Failure):
        return store.failure(payload)

    @app.post(PREFIX + "/jobs/rebuild", dependencies=[Depends(require("worker"))])
    def rebuild(payload: Rebuild):
        return store.rebuild(payload)

    @app.post(PREFIX + "/editions", dependencies=[Depends(require("worker"))])
    def publish(payload: Edition):
        return store.publish(payload)

    @app.get(PREFIX + "/read", dependencies=[Depends(require("read"))])
    def read(date: str = "today", section: int = Query(default=1, ge=1, le=1000), topic: str = Query(default="all", min_length=1, max_length=40), view: str = "daily"):
        try:
            request = ReadRequest(date=date, section=section, topic=topic, view=view)
        except ValueError:
            raise HTTPException(422, detail="invalid_read_parameters") from None
        return store.read(request)

    @app.post(PREFIX + "/read", dependencies=[Depends(require("read"))])
    def read_post(payload: ReadRequest):
        return store.read(payload)

    return app
