"""Google 로그인, 서버 세션, 역할 검사.

- 브라우저는 Google Identity Services로 받은 ID 토큰을 보낸다. 서버가 검증한 뒤
  users 표에 있는 이메일만 세션을 만든다.
- 쿠키에는 임의 토큰만 넣고 DB에는 그 해시를 저장한다.
- 권한은 화면이 아니라 여기 의존성(current_user, require_admin)으로 막는다.
"""
import hashlib
import secrets
import sqlite3
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .config import Settings
from .db import connect, now_ms

COOKIE = 'wt_session'

TokenVerifier = Callable[[str, str], dict[str, Any]]


def google_verifier(credential: str, client_id: str) -> dict[str, Any]:
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(credential, g_requests.Request(), client_id)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request):
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(request: Request, db: sqlite3.Connection = Depends(get_db)) -> dict:
    token = request.cookies.get(COOKIE)
    if token:
        row = db.execute(
            'SELECT u.email, u.name, u.role FROM sessions s JOIN users u ON u.email = s.email '
            'WHERE s.token_hash = ? AND s.expires_at > ?',
            (_hash(token), now_ms()),
        ).fetchone()
        if row:
            return dict(row)
    raise HTTPException(401, '로그인이 필요해요.')


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user['role'] != 'admin':
        raise HTTPException(403, '관리자만 사용할 수 있어요.')
    return user


router = APIRouter(prefix='/api')


class LoginBody(BaseModel):
    credential: str


@router.post('/auth/google')
def login(body: LoginBody, request: Request, response: Response,
          db: sqlite3.Connection = Depends(get_db),
          settings: Settings = Depends(get_settings)):
    if not settings.google_client_id:
        raise HTTPException(500, '서버에 GOOGLE_CLIENT_ID가 설정되지 않았어요.')
    verify: TokenVerifier = request.app.state.verify_token
    try:
        claims = verify(body.credential, settings.google_client_id)
    except Exception:
        raise HTTPException(401, 'Google 로그인 정보를 확인하지 못했어요. 다시 로그인해 주세요.')
    email = str(claims.get('email') or '').strip().lower()
    if not email or not claims.get('email_verified'):
        raise HTTPException(401, '이메일이 확인된 Google 계정으로 로그인해 주세요.')

    user = db.execute('SELECT email, name, role FROM users WHERE email = ?', (email,)).fetchone()
    if not user:
        raise HTTPException(403, f'등록되지 않은 계정이에요({email}). 관리자에게 이메일 등록을 요청하세요.')

    token = secrets.token_urlsafe(32)
    now = now_ms()
    max_age = settings.session_days * 86400
    name = str(claims.get('name') or '').strip()
    with db:
        db.execute('DELETE FROM sessions WHERE expires_at <= ?', (now,))
        db.execute(
            'INSERT INTO sessions (token_hash, email, created_at, expires_at) VALUES (?, ?, ?, ?)',
            (_hash(token), email, now, now + max_age * 1000),
        )
        # 이름은 관리자가 따로 정하지 않았을 때만 Google 프로필 이름으로 채운다.
        db.execute(
            "UPDATE users SET last_login_at = ?, name = CASE WHEN name = '' THEN ? ELSE name END "
            'WHERE email = ?',
            (now, name, email),
        )
    response.set_cookie(
        COOKIE, token, max_age=max_age, httponly=True,
        secure=settings.cookie_secure, samesite='lax', path='/',
    )
    return {'email': email, 'name': user['name'] or name, 'role': user['role']}


@router.post('/auth/logout')
def logout(request: Request, response: Response, db: sqlite3.Connection = Depends(get_db)):
    token = request.cookies.get(COOKIE)
    if token:
        with db:
            db.execute('DELETE FROM sessions WHERE token_hash = ?', (_hash(token),))
    response.delete_cookie(COOKIE, path='/')
    return {'ok': True}


@router.get('/me')
def me(user: dict = Depends(current_user)):
    return user
