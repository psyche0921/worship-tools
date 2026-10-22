"""악보 API.

- 팀원도 악보를 받고 올릴 수 있다. 다만 팀원은 드라이브에 올릴 토큰이 없어서,
  올린 파일은 서버에만 두고(`sync='pending'`) 관리자가 나중에 드라이브로 보낸다.
- 드라이브와의 매핑(연결·삭제)과 폴더 목록 갱신은 관리자만 한다.
- 서버는 드라이브를 직접 부르지 않는다. 브라우저가 부르고 결과만 여기로 알려 준다.
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
from ..db import now_ms, set_setting
from .records import service_date

router = APIRouter(prefix='/api')

MAX_NAMES = 200
MAX_INDEX = 20000


# ---------- 곡 ↔ 악보 맞추기 ----------

class MatchIn(BaseModel):
    names: list[str] = Field(default_factory=list, max_length=MAX_NAMES)


@router.post('/sheets/match')
def match(body: MatchIn, user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    """세트리스트 곡 제목을 보내면 곡마다(메들리는 쪼개서) 악보와 후보를 돌려준다."""
    return {'items': sh.match_names(db, [n[:300] for n in body.names[:MAX_NAMES]]),
            'indexed': sh.index_count(db)}


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


@router.get('/sheets/pending')
def pending(user: dict = Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    """드라이브에 아직 올리지 못한 악보(팀원이 올린 것). 관리자 브라우저가 이것을 보고 올린다."""
    rows = db.execute("SELECT * FROM sheets WHERE sync <> 'drive' ORDER BY updated_at").fetchall()
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
              driveFileId: str = Form('', max_length=100), driveName: str = Form('', max_length=300),
              user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db),
              settings: Settings = Depends(get_settings)):
    """악보 사본을 저장하고 곡에 연결한다.

    `driveFileId`가 함께 오면 그 드라이브 파일과 연결된 것으로 본다(관리자만). 없으면
    서버에만 있는 상태(`pending`)로 두고, 나중에 관리자가 드라이브로 올린다.
    """
    if driveFileId and user['role'] != 'admin':
        raise HTTPException(403, '드라이브 연결은 관리자만 할 수 있어요.')
    key = sh.song_key(title)
    if not key:
        raise HTTPException(422, '곡 제목을 적어 주세요.')
    data, mime, ext = _read(file, settings.max_sheet_mb)

    name = driveName.strip() or f'{title.strip()}{ext}'
    now = now_ms()
    sync = 'drive' if driveFileId else 'pending'
    old = db.execute('SELECT id, file_path FROM sheets WHERE song_key = ?', (key,)).fetchone()
    with db:
        if old is not None:
            sheet_id = int(old['id'])
            db.execute(
                'UPDATE sheets SET title = ?, drive_file_id = ?, drive_name = ?, mime = ?, size = ?, '
                'sync = ?, sync_error = NULL, updated_at = ? WHERE id = ?',
                (title.strip(), driveFileId, name, mime, len(data), sync, now, sheet_id),
            )
        else:
            cur = db.execute(
                'INSERT INTO sheets (song_key, title, drive_file_id, drive_name, mime, size, sync, '
                'created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (key, title.strip(), driveFileId, name, mime, len(data), sync, user['email'], now, now),
            )
            sheet_id = int(cur.lastrowid)
    path = sh.write_file(settings, sheet_id, ext, data)
    alias = sh.song_key(name)
    with db:
        db.execute('UPDATE sheets SET file_path = ? WHERE id = ?', (path, sheet_id))
        # 고른 파일 이름으로도 다음에 걸리게 별칭을 남긴다(다른 곡의 키를 가리지 않을 때만).
        if alias and alias != key and not db.execute(
                'SELECT 1 FROM sheets WHERE song_key = ?', (alias,)).fetchone():
            db.execute('INSERT OR REPLACE INTO sheet_aliases (alias_key, sheet_id) VALUES (?, ?)',
                       (alias, sheet_id))
    if old is not None and old['file_path'] and old['file_path'] != path:
        sh.remove_file(old['file_path'])
    return sh.sheet_dict(db.execute('SELECT * FROM sheets WHERE id = ?', (sheet_id,)).fetchone())


class DriveLink(BaseModel):
    driveFileId: str = Field(min_length=1, max_length=100)
    driveName: str = Field('', max_length=300)


@router.post('/sheets/{sheet_id}/drive')
def link_drive(sheet_id: int, body: DriveLink, user: dict = Depends(require_admin),
               db: sqlite3.Connection = Depends(get_db)):
    """브라우저가 드라이브에 올린 뒤 그 결과를 알려 준다 → pending 해제."""
    row = db.execute('SELECT * FROM sheets WHERE id = ?', (sheet_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '악보를 찾을 수 없어요.')
    with db:
        db.execute("UPDATE sheets SET drive_file_id = ?, drive_name = CASE WHEN ? <> '' THEN ? ELSE drive_name END, "
                   "sync = 'drive', sync_error = NULL, updated_at = ? WHERE id = ?",
                   (body.driveFileId, body.driveName.strip(), body.driveName.strip(), now_ms(), sheet_id))
    return sh.sheet_dict(db.execute('SELECT * FROM sheets WHERE id = ?', (sheet_id,)).fetchone())


@router.delete('/sheets/{sheet_id}')
def delete_sheet(sheet_id: int, user: dict = Depends(require_admin),
                 db: sqlite3.Connection = Depends(get_db)):
    """매핑과 서버 사본을 지운다. 드라이브의 원본은 건드리지 않는다."""
    row = db.execute('SELECT file_path FROM sheets WHERE id = ?', (sheet_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '악보를 찾을 수 없어요.')
    with db:
        db.execute('DELETE FROM sheets WHERE id = ?', (sheet_id,))  # 별칭은 외래 키로 함께 지워진다
    sh.remove_file(row['file_path'])
    return {'ok': True}


# ---------- 악보 받기 ----------

@router.get('/sheets/{sheet_id}/file')
def sheet_file(sheet_id: int, user: dict = Depends(current_user),
               db: sqlite3.Connection = Depends(get_db)):
    row = db.execute('SELECT * FROM sheets WHERE id = ?', (sheet_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '악보를 찾을 수 없어요.')
    if not row['file_path'] or not Path(row['file_path']).is_file():
        raise HTTPException(404, '악보 파일이 없어요. 다시 올려 주세요.')
    return FileResponse(row['file_path'], media_type=row['mime'],
                        filename=sh.safe_name(row['title'], row['mime']))


@router.get('/sheets/zip/{key}')
def setlist_zip(key: str, user: dict = Depends(current_user),
                db: sqlite3.Connection = Depends(get_db)):
    """그 주 세트리스트의 악보를 묶어서 내려준다."""
    row = db.execute('SELECT title, songs_json, created_at FROM setlists WHERE playlist_id = ?',
                     (key,)).fetchone()
    if row is None:
        raise HTTPException(404, '기록을 찾을 수 없어요.')
    songs = sh.resolve_songs(db, json.loads(row['songs_json']))
    ids: list[int] = []
    for song in songs:
        for s in song['sheets']:
            if s.get('hasFile') and s['id'] not in ids:
                ids.append(int(s['id']))
    rows = {}
    if ids:
        marks = ','.join('?' * len(ids))
        rows = {r['id']: r for r in db.execute(f'SELECT * FROM sheets WHERE id IN ({marks})', ids)}

    buf, used = io.BytesIO(), set()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for sheet_id in ids:
            r = rows.get(sheet_id)
            if r is None or not r['file_path'] or not Path(r['file_path']).is_file():
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


# ---------- 드라이브 폴더 목록 ----------

class DriveFile(BaseModel):
    id: str = Field(max_length=100)
    name: str = Field(max_length=300)
    mimeType: str = Field('', max_length=120)
    size: int | str | None = None
    modifiedTime: str | None = Field(None, max_length=40)


class IndexIn(BaseModel):
    files: list[DriveFile] = Field(default_factory=list, max_length=MAX_INDEX)
    replace: bool = False


@router.put('/drive/index')
def put_index(body: IndexIn, user: dict = Depends(require_admin),
              db: sqlite3.Connection = Depends(get_db)):
    """관리자가 Picker에서 고른 악보 파일을 목록에 넣는다.

    `drive.file` 범위는 **파일 하나하나에 권한을 주기 때문에** 폴더를 골라도 그 안을
    읽을 수 없다(2026-10-22 실제로 확인). 그래서 목록은 '관리자가 Picker에서 고른,
    곧 이 앱이 쓸 수 있는 악보'다. 여러 번에 걸쳐 고를 수 있으니 기본은 **덧붙이기**이고,
    `replace=true`면 통째로 바꾼다.
    """
    now = now_ms()
    rows, seen = [], set()
    for f in body.files:
        # 폴더와 구글 문서는 악보가 아니다.
        if f.mimeType.startswith('application/vnd.google-apps') or f.id in seen:
            continue
        seen.add(f.id)
        rows.append((f.id, f.name, sh.song_key(f.name), f.mimeType,
                     sh.as_int(f.size), sh.as_ms(f.modifiedTime), now))
    with db:
        if body.replace:
            db.execute('DELETE FROM drive_index')
        db.executemany(
            'INSERT OR REPLACE INTO drive_index '
            '(file_id, name, name_key, mime, size, modified_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)', rows)
        set_setting(db, 'drive_index_at', str(now), user['email'])
    return {'added': len(rows), 'count': sh.index_count(db), 'at': now}
