"""관리자 전용: 설정(SKILL.md·모델·자동 기획)과 팀원 관리.

세트리스트 기록은 routes/setlists.py로 옮겼다(팀원도 만들 수 있게 되었다).
"""
import re
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import get_db, get_settings, require_admin
from .. import youtube
from ..auto import AUTO_EMAIL, last_run
from ..config import Settings
from ..db import get_setting, now_ms, set_setting

router = APIRouter(prefix='/api', dependencies=[Depends(require_admin)])

EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
MODEL = re.compile(r'^[\w.\-]{1,100}$')


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
        'maxSheetMb': settings.max_sheet_mb,
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



# ---------- 찬양팀 (BBC Score) ----------
# 사이트 팀원(users)과 따로 둔다. 찬양팀원은 이 사이트가 아니라 BBC Score(악보 필기 앱)에
# 로그인하고, BBC Score 서버가 /api/integration/team/{email}로 명단을 확인한다.

def list_team(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute('SELECT t.email, t.name, t.created_at, u.role FROM team_members t '
                      'LEFT JOIN users u ON u.email = t.email ORDER BY t.created_at, t.email').fetchall()
    return [{'email': r['email'], 'name': r['name'], 'createdAt': r['created_at'],
             'siteRole': r['role'] or ''} for r in rows]


@router.get('/team')
def get_team(db: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    return {'items': list_team(db), 'scoreOrigin': settings.score_origin,
            'linked': bool(settings.score_api_key)}


@router.post('/team')
def add_team(body: MemberIn, user: dict = Depends(require_admin),
             db: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    email = body.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(400, '이메일 주소를 확인하세요.')
    try:
        with db:
            db.execute('INSERT INTO team_members (email, name, created_by, created_at) VALUES (?, ?, ?, ?)',
                       (email, body.name.strip(), user['email'], now_ms()))
    except sqlite3.IntegrityError:
        raise HTTPException(409, '이미 찬양팀에 있는 이메일이에요.')
    return get_team(db, settings)


@router.delete('/team/{email}')
def remove_team(email: str, db: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    with db:
        cur = db.execute('DELETE FROM team_members WHERE email = ?', (email.strip().lower(),))
    if not cur.rowcount:
        raise HTTPException(404, '찬양팀에 없는 이메일이에요.')
    return get_team(db, settings)
