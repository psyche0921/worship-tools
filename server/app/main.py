"""worship-tools v2 서버.

실행: uvicorn app.main:app  (server/ 디렉토리에서, 워커 1개)
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, gemini
from .config import Settings
from .db import connect, init_db, seed_admins
from .jobs import JobRunner, mark_interrupted
from .routes import shorts

# 정적 파일 위치: 저장소 루트(web/, vendor/, fonts/). 컨테이너에서는 STATIC_ROOT로 지정한다.
STATIC_ROOT = Path(os.environ.get('STATIC_ROOT') or Path(__file__).resolve().parents[2])

SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


def create_app(settings: Settings | None = None,
               verify_token: auth.TokenVerifier = auth.google_verifier,
               gemini_call: gemini.GeminiCall | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(settings.db_path)
        conn = connect(settings.db_path)
        try:
            seed_admins(conn, settings.admin_emails)
            mark_interrupted(conn)
        finally:
            conn.close()
        yield
        for task in list(app.state.jobs.tasks):
            task.cancel()

    app = FastAPI(title='worship-tools', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.verify_token = verify_token
    app.state.gemini = gemini_call or gemini.make_client(settings.gemini_api_key)
    app.state.jobs = JobRunner(app)

    @app.middleware('http')
    async def check_origin(request: Request, call_next):
        # 상태를 바꾸는 API 요청은 우리 사이트에서 온 것만 받는다(CSRF 방지).
        if request.method not in SAFE_METHODS and request.url.path.startswith('/api/'):
            if request.headers.get('origin', '').rstrip('/') != settings.public_origin:
                return JSONResponse({'detail': '허용되지 않은 요청이에요.'}, status_code=403)
        return await call_next(request)

    @app.get('/api/health')
    def health():
        return {'ok': True}

    @app.get('/api/config')
    def config():
        return {'clientId': settings.google_client_id}

    app.include_router(auth.router)
    app.include_router(shorts.router)

    for name in ('vendor', 'fonts'):
        if (STATIC_ROOT / name).is_dir():
            app.mount(f'/{name}', StaticFiles(directory=STATIC_ROOT / name), name=name)
    if (STATIC_ROOT / 'web').is_dir():
        app.mount('/', StaticFiles(directory=STATIC_ROOT / 'web', html=True), name='web')

    return app


# DB 초기화는 lifespan에서 하므로 import만으로는 파일을 건드리지 않는다.
app = create_app()
