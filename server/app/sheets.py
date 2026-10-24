"""악보: 곡 제목 정규화, 메들리 분리, 후보 찾기, 파일 보관.

악보는 서버에 직접 올린다(docs/setlist-share.md 6장). 같은 곡이 다시 나오면 곡
제목으로 저절로 붙고, 제목이 조금 다르면 **이미 올라온 악보**를 후보로 보여 준다.
"""
import difflib
import hashlib
import os
import re
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path

# 괄호 안은 (Live), (G key), [찬양]처럼 곡 이름이 아니라 덧붙인 설명이다.
BRACKETS = re.compile(r'[(\[{][^)\]}]*[)\]}]')
# 띄어쓰기 차이('새 힘 얻으리' ↔ '새힘얻으리')를 흡수하려고 공백과 기호를 모두 지운다.
DROP = re.compile(r'[\s·‧∙•・ー\-–—_.,!?\'"“”‘’~^*|:;#/\\()\[\]{}]+')
EXT = re.compile(r'\.(pdf|png|jpe?g|gif|webp|musicxml|mxl|xml)$', re.I)
# 한 줄에 두 곡이 붙어 있는 표기: '나로부터 & 기대', '비전 + Holy Forever'
MEDLEY = re.compile(r'\s*[&+]\s*|\s+/\s+')
# 줄머리의 번호나 글머리표
LEAD = re.compile(r'^\s*(?:\d{1,2}\s*[.)]|[-–—*•])\s*')

MIN_PART = 2        # 메들리로 쪼갠 조각이 이보다 짧으면 쪼개지 않는다(R&B 같은 경우)
SIM_MIN = 0.5       # '비슷한 이름' 후보로 볼 최소 비율
MAX_CANDIDATES = 5

# 올릴 수 있는 형식. 클라이언트가 말한 타입을 믿지 않고 앞머리 바이트로 가린다.
KINDS = (
    ('application/pdf', '.pdf', b'%PDF'),
    ('image/png', '.png', b'\x89PNG\r\n\x1a\n'),
    ('image/jpeg', '.jpg', b'\xff\xd8\xff'),
)
EXT_OF = {mime: ext for mime, ext, _ in KINDS}


# ---------- 곡 제목 ----------

def song_key(name: str) -> str:
    """곡 제목과 파일 이름을 같은 규칙으로 줄여서 비교할 수 있게 한다."""
    s = unicodedata.normalize('NFC', str(name or '')).strip()
    s = EXT.sub('', s)
    key = DROP.sub('', BRACKETS.sub(' ', s)).lower()
    if not key:  # 제목이 통째로 괄호 안에 있던 경우
        key = DROP.sub('', s).lower()
    return key


def split_songs(name: str) -> list[str]:
    """메들리는 곡마다 악보가 따로 있으니 쪼갠다. '나로부터 & 기대' → ['나로부터', '기대']"""
    raw = LEAD.sub('', unicodedata.normalize('NFC', str(name or '')).strip())
    if not raw:
        return []
    parts = [p.strip() for p in MEDLEY.split(raw) if p.strip()]
    if len(parts) > 1 and all(len(p) >= MIN_PART and song_key(p) for p in parts):
        return parts
    return [raw]


# ---------- 매핑 찾기 ----------

def find_sheet(db: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    if not key:
        return None
    row = db.execute('SELECT * FROM sheets WHERE song_key = ?', (key,)).fetchone()
    if row:
        return row
    return db.execute(
        'SELECT s.* FROM sheet_aliases a JOIN sheets s ON s.id = a.sheet_id WHERE a.alias_key = ?',
        (key,),
    ).fetchone()


def sheet_version(row: sqlite3.Row) -> str:
    """악보 파일의 버전(BBC Score가 캐시와 필기 위치를 맞추는 데 쓴다). 내용이 같으면
    같다 — 같은 파일을 다시 올려도 바뀌지 않는다. 해시를 아직 못 채웠으면 올린 시각."""
    return (row['sha256'] or '')[:16] or f"t{row['updated_at']}"


def sheet_dict(row: sqlite3.Row) -> dict:
    return {
        'id': row['id'], 'title': row['title'], 'songKey': row['song_key'],
        'name': row['file_name'] or (row['title'] + EXT_OF.get(row['mime'], '')),
        'mime': row['mime'], 'size': row['size'], 'version': sheet_version(row),
        'hasFile': bool(row['file_path']), 'at': row['updated_at'], 'by': row['created_by'],
    }


SHEET_COLS = 'id, title, song_key, file_name, mime, size, file_path, updated_at FROM sheets'


def _cand(row: sqlite3.Row, score: float) -> dict:
    return {'sheetId': row['id'], 'title': row['title'],
            'name': row['file_name'] or (row['title'] + EXT_OF.get(row['mime'], '')),
            'mime': row['mime'], 'size': row['size'], 'at': row['updated_at'], 'score': score}


def candidates(db: sqlite3.Connection, key: str, limit: int = MAX_CANDIDATES) -> list[dict]:
    """**이미 올라온 악보** 중에서 이 곡에 쓸 만한 것을 찾는다.

    같은 곡이 다시 세트리스트에 나오면 `find_sheet`가 바로 붙이고, 제목이 조금 다르면
    여기서 후보로 보여 준다(사용자가 고르면 별칭이 생겨 다음부터 자동으로 붙는다).
    먼저 '곡 키가 들어 있는지'를 보고, 없으면 글자 유사도로 비슷한 것을 보여 준다.
    """
    if not key:
        return []
    rows = db.execute(
        f'SELECT {SHEET_COLS} WHERE song_key <> ? AND instr(song_key, ?) > 0 '
        'ORDER BY length(song_key), updated_at DESC LIMIT ?', (key, key, limit),
    ).fetchall()
    if rows:
        return [_cand(r, 0.9) for r in rows]

    scored = []
    for r in db.execute(f'SELECT {SHEET_COLS}'):
        m = difflib.SequenceMatcher(None, key, r['song_key'])
        if m.real_quick_ratio() < SIM_MIN or m.quick_ratio() < SIM_MIN:
            continue
        ratio = m.ratio()
        if ratio >= SIM_MIN:
            scored.append((ratio, r))
    scored.sort(key=lambda p: (-p[0], p[1]['title']))
    return [_cand(r, round(ratio, 3)) for ratio, r in scored[:limit]]


def match_names(db: sqlite3.Connection, names: list[str]) -> list[dict]:
    """세트리스트 곡 제목마다 메들리를 쪼개고 악보를 찾는다."""
    out = []
    for raw in names:
        songs = []
        for title in split_songs(raw):
            key = song_key(title)
            row = find_sheet(db, key)
            songs.append({'title': title, 'songKey': key,
                          'sheet': sheet_dict(row) if row is not None else None,
                          'candidates': [] if row is not None else candidates(db, key)})
        out.append({'name': raw, 'songs': songs})
    return out


def resolve_songs(db: sqlite3.Connection, songs: list[dict]) -> list[dict]:
    """기록의 곡마다 악보를 채운다.

    기록에 저장해 둔 sheetId가 있으면 그것을 쓰고(그 주에 실제로 쓴 악보), 없으면 지금
    매핑에서 곡 제목으로 찾는다(`auto`). 나중에 올린 악보가 그 곡이 나왔던 주에도 보이게.
    지워진 악보는 `missing`으로 표시한다.
    """
    ids = {s['sheetId'] for song in songs for s in (song.get('sheets') or []) if s.get('sheetId')}
    rows: dict[int, sqlite3.Row] = {}
    if ids:
        marks = ','.join('?' * len(ids))
        rows = {r['id']: r for r in db.execute(f'SELECT * FROM sheets WHERE id IN ({marks})', list(ids))}
    for song in songs:
        stored = song.get('sheets') or []
        filled = []
        if stored:
            for s in stored:
                row = rows.get(s.get('sheetId'))
                filled.append(sheet_dict(row) if row is not None else
                              {'id': s.get('sheetId'), 'title': s.get('title') or '',
                               'missing': True, 'hasFile': False})
        else:
            for title in split_songs(song.get('name') or ''):
                row = find_sheet(db, song_key(title))
                if row is not None:
                    filled.append(sheet_dict(row) | {'auto': True})
        song['sheets'] = filled
        # 아직 악보가 없는 곡(메들리는 한쪽만 없을 수도 있다). 화면의 '올리기' 줄에 쓴다.
        have = {s.get('songKey') or song_key(s.get('title') or '') for s in filled}
        song['needSheets'] = [t for t in split_songs(song.get('name') or '')
                              if song_key(t) not in have]
    return songs


def count_linked(db: sqlite3.Connection, songs: list[dict]) -> int:
    """악보가 붙은 곡이 몇인지(목록의 '악보 2/3' 표시용). 저장된 것이 없으면 제목으로 찾아 센다."""
    n = 0
    for song in songs:
        stored = song.get('sheets') or []
        if stored:
            n += len(stored)
        else:
            n += sum(1 for t in split_songs(song.get('name') or '')
                     if find_sheet(db, song_key(t)) is not None)
    return n


# ---------- 파일 ----------

def sniff(data: bytes) -> tuple[str, str] | None:
    """앞머리 바이트로 형식을 가린다. 못 알아보면 None."""
    for mime, ext, magic in KINDS:
        if data.startswith(magic):
            return mime, ext
    return None


def _dir(settings) -> Path:
    path = Path(settings.sheets_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_file(settings, sheet_id: int, ext: str, data: bytes) -> str:
    path = _dir(settings) / f'{sheet_id}{ext}'
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return str(path)


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fill_sheet_hashes(conn: sqlite3.Connection) -> int:
    """해시가 비어 있는 악보(마이그레이션 전에 올린 것)를 채운다. 서버 시작 때 한 번."""
    rows = conn.execute("SELECT id, file_path FROM sheets WHERE sha256 = '' AND file_path <> ''").fetchall()
    n = 0
    for r in rows:
        try:
            digest = file_hash(Path(r['file_path']).read_bytes())
        except OSError:
            continue  # 파일이 없으면 받을 때 404가 나니 버전도 의미가 없다
        with conn:
            conn.execute('UPDATE sheets SET sha256 = ? WHERE id = ?', (digest, r['id']))
        n += 1
    return n


def remove_file(path: str) -> None:
    try:
        if path:
            Path(path).unlink(missing_ok=True)
    except OSError:
        pass  # 사본이 없어도 매핑은 지워야 한다


def safe_name(title: str, mime: str) -> str:
    """내려받을 때 쓸 파일 이름. 경로 구분자 같은 글자는 뺀다."""
    base = re.sub(r'[\\/:*?"<>|\r\n\t]+', ' ', str(title or '악보')).strip() or '악보'
    return base[:120] + EXT_OF.get(mime, '')
