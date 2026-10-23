"""세트리스트 기록. 팀원도 만들 수 있다.

유튜브 재생목록은 더 만들지 않는다(`docs/setlist-share.md`). 결과물은 공유 페이지이고,
기록의 키는 그 공유 토큰(`share_token`)이다. 예전에 만든 재생목록은 `playlist_id`에
남아 있고 공유 페이지에서 링크로 함께 보여 준다.
"""
import json
import secrets
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import sheets, setlistmeta
from ..auth import current_user, get_db, get_settings
from ..config import Settings
from ..db import now_ms

router = APIRouter(prefix='/api')


class SongSheet(BaseModel):
    """그 곡에 연결한 악보. 메들리는 곡마다 하나씩이라 목록이다."""
    sheetId: int = Field(ge=1)
    title: str = Field('', max_length=300)


class Song(BaseModel):
    id: str = Field(pattern=r'^[\w-]{11}$')
    name: str = Field('', max_length=300)
    notes: list[str] = Field(default_factory=list, max_length=50)
    sheets: list[SongSheet] = Field(default_factory=list, max_length=10)


class SetlistIn(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    songs: list[Song] = Field(min_length=1, max_length=200)
    rawMessage: str = Field('', max_length=50000)


def share_url(settings: Settings, token: str) -> str:
    return f'{settings.public_origin}/s/{token}'


def setlist_item(row: sqlite3.Row, with_raw: bool = False,
                 db: sqlite3.Connection | None = None,
                 settings: Settings | None = None) -> dict:
    songs = json.loads(row['songs_json'])
    if db is not None:
        sheets.resolve_songs(db, songs)  # 저장된 sheetId, 없으면 곡 제목으로 지금 악보를 찾는다
    item = {'key': row['share_token'], 'shareToken': row['share_token'],
            'title': row['title'], 'at': row['created_at'],
            'total': row['total'], 'ok': row['ok'], 'songs': songs,
            'playlistId': row['playlist_id'] or '', 'url': row['url'] or ''}
    if settings is not None:
        item['shareUrl'] = share_url(settings, row['share_token'])
    if with_raw:
        item['rawMessage'] = row['raw_message']
    return item


def find(db: sqlite3.Connection, token: str) -> sqlite3.Row:
    row = db.execute('SELECT * FROM setlists WHERE share_token = ?', (token,)).fetchone()
    if not row:
        raise HTTPException(404, '기록을 찾을 수 없어요.')
    return row


@router.get('/setlists')
def list_setlists(before: int | None = None, limit: int = Query(20, ge=1, le=100),
                  user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db),
                  settings: Settings = Depends(get_settings)):
    rows = db.execute(
        'SELECT * FROM setlists WHERE created_at < ? ORDER BY created_at DESC, id DESC LIMIT ?',
        (before if before is not None else 2**62, limit + 1),
    ).fetchall()
    return {'items': [setlist_item(r, settings=settings) for r in rows[:limit]],
            'more': len(rows) > limit}


@router.get('/setlists/{token}')
def get_setlist(token: str, user: dict = Depends(current_user),
                db: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    row = find(db, token)
    item = setlist_item(row, with_raw=user['role'] == 'admin', db=db, settings=settings)
    item['canDelete'] = user['role'] == 'admin' or row['created_by'] == user['email']
    return item


@router.post('/setlists')
def save_setlist(body: SetlistIn, user: dict = Depends(current_user),
                 db: sqlite3.Connection = Depends(get_db),
                 settings: Settings = Depends(get_settings)):
    """세트리스트를 만들고 공유 주소를 돌려준다. 유튜브는 건드리지 않는다."""
    songs = json.dumps([s.model_dump() for s in body.songs], ensure_ascii=False)
    token = secrets.token_urlsafe(16)
    with db:
        db.execute(
            'INSERT INTO setlists (share_token, title, total, ok, songs_json, raw_message, '
            'created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (token, body.title, len(body.songs), len(body.songs), songs, body.rawMessage,
             user['email'], now_ms()),
        )
    return get_setlist(token, user, db, settings)


@router.delete('/setlists/{token}')
def delete_setlist(token: str, user: dict = Depends(current_user),
                   db: sqlite3.Connection = Depends(get_db)):
    row = find(db, token)
    if user['role'] != 'admin' and row['created_by'] != user['email']:
        raise HTTPException(403, '본인이 만든 기록만 지울 수 있어요.')
    with db:
        db.execute('DELETE FROM setlists WHERE share_token = ?', (token,))
    return {'ok': True}


# ---------- 공유 페이지가 읽는 JSON (누구나) ----------

WATCH_ALL = 'https://www.youtube.com/watch_videos?video_ids='


def share_payload(row: sqlite3.Row, db: sqlite3.Connection) -> dict:
    """로그인 없이 내려주는 내용. 단톡방 원문(raw_message)은 넣지 않는다.

    이 모양이 drdr 연동 계약이다(`docs/setlist-share.md` 5장).
    """
    from .records import _dates  # 순환 import를 피하려고 여기서 가져온다

    token = row['share_token']
    songs = sheets.resolve_songs(db, json.loads(row['songs_json']))
    out = []
    for song in songs:
        notes = song.get('notes') or []
        meta = setlistmeta.parse(notes)
        vid = song.get('id') or ''
        out.append({
            'videoId': vid,
            'youtubeUrl': f'https://youtu.be/{vid}' if vid else '',
            'name': song.get('name') or '',
            'titles': sheets.split_songs(song.get('name') or ''),
            'notes': notes,
            'sheets': [{'title': s.get('title') or '', 'name': s.get('name') or '',
                        'mime': s.get('mime') or '', 'size': s.get('size') or 0,
                        'url': f"/api/share/{token}/sheet/{s['id']}"}
                       for s in song['sheets'] if s.get('hasFile')],
            **meta,
        })
    ids = [s['videoId'] for s in out if s['videoId']]
    has_sheet = any(s['sheets'] for s in out)
    return {
        'token': token,
        'title': row['title'],
        'songs': out,
        'playlistUrl': row['url'] or '',
        'watchAllUrl': (WATCH_ALL + ','.join(ids)) if ids else '',
        'sheetsZipUrl': f'/api/share/{token}/sheets.zip' if has_sheet else '',
        'createdAt': row['created_at'],
        **_dates('setlist', row['title'], row['created_at']),
    }


@router.get('/share/{token}')
def share(token: str, db: sqlite3.Connection = Depends(get_db)):
    """로그인 없이 누구나. 토큰이 추측 불가능한 것이 유일한 자물쇠다."""
    return share_payload(find(db, token), db)
