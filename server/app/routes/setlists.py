"""세트리스트 기록. 팀원도 만들 수 있다.

유튜브 재생목록은 더 만들지 않는다(`docs/setlist-share.md`). 결과물은 공유 페이지이고,
기록의 키는 그 공유 토큰(`share_token`)이다. 예전에 만든 재생목록은 `playlist_id`에
남아 있고 공유 페이지에서 링크로 함께 보여 준다.
"""
import json
import secrets
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import sheets, setlistmeta, youtube
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
        item['scoreUrl'] = score_url(settings, row['share_token'])
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
            # id·version은 BBC Score가 쓴다(반복곡은 같은 id, 파일이 바뀌면 version이 바뀐다).
            'sheets': [{'id': s['id'], 'version': s.get('version') or '',
                        'title': s.get('title') or '', 'name': s.get('name') or '',
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


def score_url(settings: Settings, token: str) -> str:
    """BBC Score(악보 필기 앱)에서 이 세트리스트를 여는 주소. 앱 주소를 설정하지 않았으면 빈 값."""
    return f'{settings.score_origin}/?s={token}' if settings.score_origin else ''


@router.get('/share/{token}')
def share(token: str, db: sqlite3.Connection = Depends(get_db),
          settings: Settings = Depends(get_settings)):
    """로그인 없이 누구나. 토큰이 추측 불가능한 것이 유일한 자물쇠다."""
    return share_payload(find(db, token), db) | {'scoreUrl': score_url(settings, token)}


# ---------- drdr가 새 세트리스트를 찾아 가는 목록 (비밀 키) ----------

FEED_MAX = 200


@router.get('/drdr/setlists')
def drdr_feed(request: Request, since: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=FEED_MAX),
              db: sqlite3.Connection = Depends(get_db),
              settings: Settings = Depends(get_settings)):
    """drdr(채보 학습)가 세트리스트를 스스로 찾아 가는 목록(`docs/setlist-share.md` 5장).

    세트리스트를 만들면 drdr가 몇 분 안에 이 목록에서 보고 공유 JSON을 읽어 간다 — 관리자가 공유 주소를
    붙여 넣지 않아도 된다. 공유 토큰을 모두 담으므로 비밀 키(`DRDR_FEED_TOKEN`)를 건다.
    키가 없으면 목록 자체가 없다(404). `since`(ms)보다 나중에 만든 것을 오래된 순서로.
    """
    key = settings.drdr_feed_token
    if not key:
        raise HTTPException(404, 'Not Found')
    got = request.headers.get('authorization', '')
    if not got.startswith('Bearer ') or not secrets.compare_digest(got[7:].encode(), key.encode()):
        raise HTTPException(401, '키가 맞지 않아요.')
    rows = db.execute(
        'SELECT share_token, title, total, created_at FROM setlists WHERE created_at > ? '
        'ORDER BY created_at, id LIMIT ?', (since, limit + 1)).fetchall()
    return {'items': [{'token': r['share_token'], 'title': r['title'], 'songs': r['total'],
                       'createdAt': r['created_at'], 'shareUrl': share_url(settings, r['share_token'])}
                      for r in rows[:limit]],
            'more': len(rows) > limit}


# 영상 ID 목록 -> (만든 시각, 임시 재생목록 ID). 유튜브를 매번 부르지 않으려고 잠깐 둔다.
_LIST_CACHE: dict[str, tuple[float, str]] = {}
LIST_TTL = 3600.0


@router.get('/share/{token}/youtube')
async def share_youtube(token: str, request: Request,
                        db: sqlite3.Connection = Depends(get_db)):
    """공유 페이지가 '유튜브 앱에서 연속 재생' 단추에 쓸 주소.

    유튜브에 임시 재생목록(TLGG…)을 만들게 하고 평범한 재생목록 주소로 돌려준다.
    휴대폰에서 이 주소를 누르면 브라우저가 아니라 **유튜브 앱**이 열려서 화면을 꺼도
    계속 들린다. 로그인 없이 누구나(공유 토큰이 자물쇠).
    """
    row = find(db, token)
    ids = [s.get('id') for s in json.loads(row['songs_json']) if s.get('id')]
    if not ids:
        raise HTTPException(404, '영상이 없어요.')
    key = ','.join(ids)
    hit = _LIST_CACHE.get(key)
    if hit and time.time() - hit[0] < LIST_TTL:
        list_id = hit[1]
    else:
        resolve = getattr(request.app.state, 'yt_temp_list', None) or youtube.make_temp_list()
        try:
            list_id = await resolve(ids)
        except youtube.YoutubeError as e:
            raise HTTPException(502, str(e))
        _LIST_CACHE[key] = (time.time(), list_id)
    return {
        'listId': list_id,
        'url': f'https://www.youtube.com/watch?v={ids[0]}&list={list_id}',
        'playlistUrl': f'https://www.youtube.com/playlist?list={list_id}',
    }
