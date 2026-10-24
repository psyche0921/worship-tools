"""악보 API.

악보는 **서버에 바로 올립니다**(`docs/setlist-share.md` 6장). 팀원도 올리고 받을 수 있습니다.

공유 페이지용 경로(`/api/share/...`)는 **로그인 없이 누구나** 쓸 수 있습니다. 추측
불가능한 공유 토큰이 유일한 자물쇠이고, 그 세트리스트에 붙은 악보만 내려줍니다
(번호를 바꿔 다른 악보를 엿볼 수 없습니다).
"""
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import sheets as sh
from ..auth import current_user, get_db, get_settings, require_admin
from ..config import Settings
from ..db import now_ms
from .records import service_date

router = APIRouter(prefix='/api')

MAX_NAMES = 200


# ---------- 곡 ↔ 악보 맞추기 ----------

class MatchIn(BaseModel):
    names: list[str] = Field(default_factory=list, max_length=MAX_NAMES)


@router.post('/sheets/match')
def match(body: MatchIn, user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    """세트리스트 곡 제목을 보내면 곡마다(메들리는 쪼개서) 악보와 후보를 돌려준다."""
    return {'items': sh.match_names(db, [n[:300] for n in body.names[:MAX_NAMES]]),
            'indexed': int(db.execute('SELECT count(*) FROM sheets').fetchone()[0])}


@router.get('/sheets')
def list_sheets(q: str = Query('', max_length=100), user: dict = Depends(current_user),
                db: sqlite3.Connection = Depends(get_db)):
    key = sh.song_key(q)
    if key:
        rows = db.execute('SELECT * FROM sheets WHERE song_key LIKE ? ORDER BY title LIMIT 100',
                          (f'%{key}%',)).fetchall()
    else:
        rows = db.execute('SELECT * FROM sheets ORDER BY title LIMIT 100').fetchall()
    return {'items': [sh.sheet_dict(r) for r in rows]}


# ---------- 악보 올리기 ----------

def _read(file: UploadFile, limit_mb: int) -> tuple[bytes, str, str]:
    cap = limit_mb * 1024 * 1024
    data = file.file.read(cap + 1)
    if len(data) > cap:
        raise HTTPException(413, f'악보는 {limit_mb}MB까지 올릴 수 있어요.')
    if not data:
        raise HTTPException(422, '빈 파일이에요.')
    kind = sh.sniff(data)
    if not kind:
        raise HTTPException(422, 'PDF 또는 PNG·JPG 파일만 올릴 수 있어요.')
    return data, kind[0], kind[1]


@router.post('/sheets')
def put_sheet(title: str = Form(max_length=300), file: UploadFile = File(),
              fileName: str = Form('', max_length=300),
              user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db),
              settings: Settings = Depends(get_settings)):
    """악보를 서버에 올리고 곡에 연결한다. 같은 곡에 이미 있으면 바꾼다."""
    key = sh.song_key(title)
    if not key:
        raise HTTPException(422, '곡 제목을 적어 주세요.')
    data, mime, ext = _read(file, settings.max_sheet_mb)

    name = (fileName.strip() or file.filename or '')[:300] or f'{title.strip()}{ext}'
    digest = sh.file_hash(data)
    now = now_ms()
    old = db.execute('SELECT id, file_path FROM sheets WHERE song_key = ?', (key,)).fetchone()
    with db:
        if old is not None:
            sheet_id = int(old['id'])
            db.execute(
                'UPDATE sheets SET title = ?, file_name = ?, mime = ?, size = ?, sha256 = ?, '
                'updated_at = ? WHERE id = ?',
                (title.strip(), name, mime, len(data), digest, now, sheet_id))
        else:
            cur = db.execute(
                'INSERT INTO sheets (song_key, title, file_name, mime, size, sha256, created_by, '
                'created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (key, title.strip(), name, mime, len(data), digest, user['email'], now, now))
            sheet_id = int(cur.lastrowid)
    path = sh.write_file(settings, sheet_id, ext, data)
    alias = sh.song_key(name)
    with db:
        db.execute('UPDATE sheets SET file_path = ? WHERE id = ?', (path, sheet_id))
        # 파일 이름으로도 다음에 걸리게 별칭을 남긴다(다른 곡의 키를 가리지 않을 때만).
        if alias and alias != key and not db.execute(
                'SELECT 1 FROM sheets WHERE song_key = ?', (alias,)).fetchone():
            db.execute('INSERT OR REPLACE INTO sheet_aliases (alias_key, sheet_id) VALUES (?, ?)',
                       (alias, sheet_id))
    if old is not None and old['file_path'] and old['file_path'] != path:
        sh.remove_file(old['file_path'])
    return sh.sheet_dict(db.execute('SELECT * FROM sheets WHERE id = ?', (sheet_id,)).fetchone())


class AliasIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)


@router.post('/sheets/{sheet_id}/alias')
def add_alias(sheet_id: int, body: AliasIn, user: dict = Depends(current_user),
              db: sqlite3.Connection = Depends(get_db)):
    """'이 곡은 이미 올라온 저 악보를 쓴다'고 이어 준다(제목이 조금 다른 같은 곡)."""
    row = db.execute('SELECT * FROM sheets WHERE id = ?', (sheet_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '악보를 찾을 수 없어요.')
    key = sh.song_key(body.title)
    if not key:
        raise HTTPException(422, '곡 제목을 적어 주세요.')
    if db.execute('SELECT 1 FROM sheets WHERE song_key = ? AND id <> ?', (key, sheet_id)).fetchone():
        raise HTTPException(409, '그 곡에는 이미 다른 악보가 있어요.')
    with db:
        db.execute('INSERT OR REPLACE INTO sheet_aliases (alias_key, sheet_id) VALUES (?, ?)',
                   (key, sheet_id))
    return sh.sheet_dict(row)


@router.delete('/sheets/{sheet_id}')
def delete_sheet(sheet_id: int, user: dict = Depends(require_admin),
                 db: sqlite3.Connection = Depends(get_db)):
    """매핑과 서버 사본을 지운다."""
    row = db.execute('SELECT file_path FROM sheets WHERE id = ?', (sheet_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '악보를 찾을 수 없어요.')
    with db:
        db.execute('DELETE FROM sheets WHERE id = ?', (sheet_id,))  # 별칭은 외래 키로 함께 지워진다
    sh.remove_file(row['file_path'])
    return {'ok': True}


# ---------- 악보 받기 ----------

def _file_response(row: sqlite3.Row) -> FileResponse:
    if not row['file_path'] or not Path(row['file_path']).is_file():
        raise HTTPException(404, '악보 파일이 없어요. 다시 올려 주세요.')
    return FileResponse(row['file_path'], media_type=row['mime'],
                        filename=sh.safe_name(row['title'], row['mime']))


@router.get('/sheets/{sheet_id}/file')
def sheet_file(sheet_id: int, user: dict = Depends(current_user),
               db: sqlite3.Connection = Depends(get_db)):
    row = db.execute('SELECT * FROM sheets WHERE id = ?', (sheet_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '악보를 찾을 수 없어요.')
    return _file_response(row)


def setlist_sheets(db: sqlite3.Connection, token: str) -> tuple[sqlite3.Row, dict[int, sqlite3.Row]]:
    """그 세트리스트에 붙은 악보만 모은다. 공유 주소로 다른 악보를 엿볼 수 없게 하는 울타리."""
    row = db.execute('SELECT * FROM setlists WHERE share_token = ?', (token,)).fetchone()
    if row is None:
        raise HTTPException(404, '기록을 찾을 수 없어요.')
    songs = sh.resolve_songs(db, json.loads(row['songs_json']))
    ids = []
    for song in songs:
        for s in song['sheets']:
            if s.get('hasFile') and s['id'] not in ids:
                ids.append(int(s['id']))
    found = {}
    if ids:
        marks = ','.join('?' * len(ids))
        found = {r['id']: r for r in db.execute(f'SELECT * FROM sheets WHERE id IN ({marks})', ids)}
    return row, {i: found[i] for i in ids if i in found}


def _zip(row: sqlite3.Row, rows: dict[int, sqlite3.Row]) -> Response:
    buf, used = io.BytesIO(), set()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for r in rows.values():
            if not r['file_path'] or not Path(r['file_path']).is_file():
                continue
            name = sh.safe_name(r['title'], r['mime'])
            stem, dot, ext = name.rpartition('.')
            n = 2
            while name in used:
                name = f'{stem} ({n}){dot}{ext}' if dot else f'{name} ({n})'
                n += 1
            used.add(name)
            zf.write(r['file_path'], name)
    if not used:
        raise HTTPException(404, '내려받을 악보가 없어요.')
    date = service_date('setlist', row['title'], row['created_at'])
    # 헤더는 latin-1로만 보낼 수 있어서 한글 파일 이름은 RFC 5987로 적는다.
    fname = quote(f'{date:%y%m%d}_악보.zip')
    return Response(buf.getvalue(), media_type='application/zip',
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{fname}"})


@router.get('/sheets/zip/{token}')
def setlist_zip(token: str, user: dict = Depends(current_user),
                db: sqlite3.Connection = Depends(get_db)):
    """로그인한 팀원이 작업 기록에서 받는 길."""
    return _zip(*setlist_sheets(db, token))


# ---------- 공유 페이지용 (누구나) ----------

@router.get('/share/{token}/sheet/{sheet_id}')
def share_sheet(token: str, sheet_id: int, db: sqlite3.Connection = Depends(get_db)):
    _row, rows = setlist_sheets(db, token)
    if sheet_id not in rows:
        raise HTTPException(404, '이 세트리스트의 악보가 아니에요.')
    return _file_response(rows[sheet_id])


@router.get('/share/{token}/sheets.zip')
def share_zip(token: str, db: sqlite3.Connection = Depends(get_db)):
    return _zip(*setlist_sheets(db, token))
