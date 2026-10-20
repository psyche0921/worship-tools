"""관리자 전용: 세트리스트 기록, 설정(SKILL.md·모델·자동 기획), 팀원 관리.

세트리스트 항목도 v1의 hist.setlist와 같은 모양이다(key=재생목록 ID, title, url, at, ok, total, songs).
"""
import json
import re
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auth import get_db, get_settings, require_admin
from .. import youtube
from ..auto import AUTO_EMAIL, last_run
from ..config import Settings
from ..db import get_setting, now_ms, set_setting

router = APIRouter(prefix='/api', dependencies=[Depends(require_admin)])

EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
MODEL = re.compile(r'^[\w.\-]{1,100}$')


# ---------- 세트리스트 ----------

class Song(BaseModel):
    id: str = Field(pattern=r'^[\w-]{11}$')
    name: str = Field('', max_length=300)
    notes: list[str] = Field(default_factory=list, max_length=50)


class SetlistIn(BaseModel):
    playlistId: str = Field(pattern=r'^[\w-]{10,64}$')
    title: str = Field(min_length=1, max_length=150)
    url: str = Field(max_length=300)
    ok: int = Field(ge=0)
    total: int = Field(ge=0)
    songs: list[Song] = Field(max_length=200)
    rawMessage: str = Field('', max_length=50000)


def setlist_item(row: sqlite3.Row, with_raw: bool = False) -> dict:
    item = {'key': row['playlist_id'], 'title': row['title'], 'url': row['url'], 'at': row['created_at'],
            'ok': row['ok'], 'total': row['total'], 'songs': json.loads(row['songs_json'])}
    if with_raw:
        item['rawMessage'] = row['raw_message']
    return item


@router.get('/setlists')
def list_setlists(before: int | None = None, limit: int = Query(20, ge=1, le=100),
                  db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        'SELECT * FROM setlists WHERE created_at < ? ORDER BY created_at DESC, id DESC LIMIT ?',
        (before if before is not None else 2**62, limit + 1),
    ).fetchall()
    return {'items': [setlist_item(r) for r in rows[:limit]], 'more': len(rows) > limit}


@router.get('/setlists/{key}')
def get_setlist(key: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute('SELECT * FROM setlists WHERE playlist_id = ?', (key,)).fetchone()
    if not row:
        raise HTTPException(404, '기록을 찾을 수 없어요.')
    return setlist_item(row, with_raw=True)


@router.post('/setlists')
def save_setlist(body: SetlistIn, user: dict = Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    songs = json.dumps([s.model_dump() for s in body.songs], ensure_ascii=False)
    with db:
        # 같은 재생목록을 다시 저장하면(곡 추가 재시도 등) 내용만 바꾸고 처음 만든 시각은 둔다.
        db.execute(
            'INSERT INTO setlists (playlist_id, title, url, ok, total, songs_json, raw_message, created_by, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(playlist_id) DO UPDATE SET '
            'title = excluded.title, url = excluded.url, ok = excluded.ok, total = excluded.total, '
            'songs_json = excluded.songs_json, raw_message = excluded.raw_message',
            (body.playlistId, body.title, body.url, body.ok, body.total, songs, body.rawMessage,
             user['email'], now_ms()),
        )
    return get_setlist(body.playlistId, db)


@router.delete('/setlists/{key}')
def delete_setlist(key: str, db: sqlite3.Connection = Depends(get_db)):
    with db:
        n = db.execute('DELETE FROM setlists WHERE playlist_id = ?', (key,)).rowcount
    if not n:
        raise HTTPException(404, '기록을 찾을 수 없어요.')
    return {'ok': True}


# ---------- 설정 ----------

class SettingsIn(BaseModel):
    skill: str | None = Field(None, max_length=200_000)
    model: str | None = Field(None, max_length=100)
    channel: str | None = Field(None, max_length=200)
    autoEnabled: bool | None = None


@router.get('/settings')
def read_settings(db: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    skill = db.execute("SELECT value, updated_at, updated_by FROM settings WHERE key = 'skill_md'").fetchone()
    return {
        'skill': skill['value'] if skill else '',
        'skillUpdatedAt': skill['updated_at'] if skill else None,
        'skillUpdatedBy': skill['updated_by'] if skill else None,
        'model': get_setting(db, 'model') or '',
        'defaultModel': settings.default_model,
        'geminiKeySet': bool(settings.gemini_api_key),
        'channel': get_setting(db, 'yt_channel') or '',
        'autoEnabled': (get_setting(db, 'auto_enabled') or '') == '1',
        'autoLast': last_run(db),
        'youtubeKeySet': bool(settings.youtube_api_key),
    }


@router.put('/settings')
def write_settings(body: SettingsIn, user: dict = Depends(require_admin),
                   db: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    model = (body.model or '').strip() if body.model is not None else None
    if model and not MODEL.match(model):
        raise HTTPException(400, '모델 이름을 확인하세요. (예: gemini-3.8-flash)')
    channel = (body.channel or '').strip() if body.channel is not None else None
    if channel:
        try:
            youtube.channel_url(channel)
        except youtube.YoutubeError as e:
            raise HTTPException(400, str(e))
    with db:
        if body.skill is not None:
            set_setting(db, 'skill_md', body.skill, user['email'])
        if model is not None:
            if model:
                set_setting(db, 'model', model, user['email'])
            else:
                db.execute("DELETE FROM settings WHERE key = 'model'")  # 비우면 기본 모델
        if channel is not None:
            set_setting(db, 'yt_channel', channel, user['email'])
        if body.autoEnabled is not None:
            set_setting(db, 'auto_enabled', '1' if body.autoEnabled else '', user['email'])
    return read_settings(db, settings)


@router.post('/auto/run')
async def run_auto(request: Request, db: sqlite3.Connection = Depends(get_db)):
    """'지금 한 번 실행'. 2~4분 걸리므로 백그라운드로 돌리고 상태만 돌려준다.

    진행 상황은 GET /api/settings의 autoLast로 확인한다(요청을 붙들고 있으면 504가 난다).
    """
    return request.app.state.auto.start_now(db)


# ---------- 팀원 ----------

class MemberIn(BaseModel):
    email: str = Field(max_length=200)
    name: str = Field('', max_length=50)


def list_members(db: sqlite3.Connection) -> list[dict]:
    # 자동 기획이 쓰는 계정은 사람이 아니라서 명단에서 뺀다.
    rows = db.execute(
        "SELECT email, name, role, created_at, last_login_at FROM users WHERE email <> ? "
        "ORDER BY role = 'admin' DESC, created_at, email", (AUTO_EMAIL,)
    ).fetchall()
    return [{'email': r['email'], 'name': r['name'], 'role': r['role'],
             'createdAt': r['created_at'], 'lastLoginAt': r['last_login_at']} for r in rows]


@router.get('/members')
def get_members(db: sqlite3.Connection = Depends(get_db)):
    return list_members(db)


@router.post('/members')
def add_member(body: MemberIn, db: sqlite3.Connection = Depends(get_db)):
    email = body.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(400, '이메일 주소를 확인하세요.')
    try:
        with db:
            db.execute("INSERT INTO users (email, name, role, created_at) VALUES (?, ?, 'member', ?)",
                       (email, body.name.strip(), now_ms()))
    except sqlite3.IntegrityError:
        raise HTTPException(409, '이미 등록된 이메일이에요.')
    return list_members(db)


@router.delete('/members/{email}')
def remove_member(email: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute('SELECT role FROM users WHERE email = ?', (email.strip().lower(),)).fetchone()
    if not row:
        raise HTTPException(404, '등록되지 않은 이메일이에요.')
    if row['role'] == 'admin':
        raise HTTPException(400, '관리자 계정은 삭제할 수 없어요.')
    with db:
        # 세션은 외래 키로 함께 지워진다. 그 사람이 만든 쇼츠 기록은 팀 기록이라 남긴다.
        db.execute('DELETE FROM users WHERE email = ?', (email.strip().lower(),))
    return list_members(db)

