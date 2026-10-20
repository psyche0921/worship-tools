"""쇼츠 기획: 작업 등록·확인, 팀 공용 기록, 설교 대본(영상 하나에 하나).

응답의 기록 항목은 v1의 hist.shorts 항목과 같은 모양이다(key, id=영상 ID, start, end, at, title,
extra, text). 화면 코드(renderShorts, buildPdf)를 그대로 쓰기 위해서다.
"""
import json
import re
import secrets
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auth import current_user, get_db, get_settings
from ..config import Settings
from ..db import get_setting, now_ms
from ..jobs import claim_transcript, kst_midnight_ms

router = APIRouter(prefix='/api')

VIDEO_ID = re.compile(r'^[\w-]{11}$')


def shorts_item(row: sqlite3.Row, user: dict, with_text: bool = False) -> dict:
    item = {
        'key': str(row['id']), 'id': row['video_id'], 'start': row['start_sec'], 'end': row['end_sec'],
        'at': row['created_at'], 'title': row['title'], 'extra': row['extra'],
        'by': row['created_by'], 'byName': row['by_name'] or row['created_by'].split('@')[0],
        'canDelete': user['role'] == 'admin' or row['created_by'] == user['email'],
    }
    if with_text:
        # 설교 대본은 영상 하나에 하나(transcripts 표)라서, 같은 영상의 다른 기획과 같은 대본을 본다.
        item['text'] = row['text']
        item['transcript'] = row['transcript'] or ''
        item['transcriptStatus'] = row['transcript_status'] or ''
        item['transcriptError'] = row['transcript_error']
    return item


SHORTS_SELECT = ('SELECT s.*, u.name AS by_name, t.text AS transcript, t.status AS transcript_status, '
                 't.error AS transcript_error FROM shorts s LEFT JOIN users u ON u.email = s.created_by '
                 'LEFT JOIN transcripts t ON t.video_id = s.video_id')


def load_shorts(db: sqlite3.Connection, shorts_id: int, user: dict) -> dict | None:
    row = db.execute(SHORTS_SELECT + ' WHERE s.id = ?', (shorts_id,)).fetchone()
    return shorts_item(row, user, with_text=True) if row else None


class JobIn(BaseModel):
    kind: Literal['plan', 'detect']
    videoId: str
    start: int | None = Field(None, ge=0)
    end: int | None = Field(None, ge=0)
    extra: str = Field('', max_length=2000)
    sermonTitle: str = Field('', max_length=200)


@router.post('/jobs')
async def create_job(body: JobIn, request: Request, user: dict = Depends(current_user),
                     db: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    if not VIDEO_ID.match(body.videoId):
        raise HTTPException(400, '유튜브 영상 링크를 확인하세요.')
    if body.start is not None and body.end is not None and body.end <= body.start:
        raise HTTPException(400, '설교 끝 시각이 시작보다 늦어야 해요.')
    busy = db.execute("SELECT 1 FROM jobs WHERE created_by = ? AND status IN ('queued', 'running')",
                      (user['email'],)).fetchone()
    if busy:
        raise HTTPException(409, '이미 진행 중인 작업이 있어요. 끝난 뒤 다시 시도하세요.')
    if body.kind == 'plan':
        if not (get_setting(db, 'skill_md') or '').strip():
            raise HTTPException(400, '관리자가 설정에서 SKILL.md를 아직 등록하지 않았어요.')
        if user['role'] != 'admin':
            used = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE created_by = ? AND kind = 'plan' AND status != 'error' "
                'AND created_at >= ?', (user['email'], kst_midnight_ms(now_ms())),
            ).fetchone()[0]
            if used >= settings.daily_plan_limit:
                raise HTTPException(429, f'오늘 쇼츠 기획 횟수({settings.daily_plan_limit}회)를 다 썼어요. 내일 다시 시도하세요.')

    inp = body.model_dump()
    if body.kind == 'detect':
        inp = {'videoId': body.videoId}
    job_id = secrets.token_urlsafe(12)
    with db:
        db.execute(
            "INSERT INTO jobs (id, kind, status, input_json, created_by, created_at) VALUES (?, ?, 'queued', ?, ?, ?)",
            (job_id, body.kind, json.dumps(inp, ensure_ascii=False), user['email'], now_ms()),
        )
    request.app.state.jobs.start(job_id)
    return {'jobId': job_id}


@router.get('/jobs/{job_id}')
def get_job(job_id: str, user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    row = db.execute('SELECT * FROM jobs WHERE id = ? AND created_by = ?', (job_id, user['email'])).fetchone()
    if not row:
        raise HTTPException(404, '작업을 찾을 수 없어요.')
    out = {'id': row['id'], 'kind': row['kind'], 'status': row['status'], 'error': row['error'],
           'result': json.loads(row['result_json']) if row['result_json'] else None}
    if row['shorts_id']:
        out['shorts'] = load_shorts(db, row['shorts_id'], user)
    return out


@router.get('/shorts')
def list_shorts(before: int | None = None, limit: int = Query(20, ge=1, le=100),
                user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        SHORTS_SELECT + ' WHERE s.created_at < ? ORDER BY s.created_at DESC, s.id DESC LIMIT ?',
        (before if before is not None else 2**62, limit + 1),
    ).fetchall()
    return {'items': [shorts_item(r, user) for r in rows[:limit]], 'more': len(rows) > limit}


@router.get('/shorts/{shorts_id}')
def get_shorts(shorts_id: int, user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    item = load_shorts(db, shorts_id, user)
    if not item:
        raise HTTPException(404, '기록을 찾을 수 없어요.')
    return item


@router.post('/shorts/{shorts_id}/transcript')
async def make_transcript(shorts_id: int, request: Request, user: dict = Depends(current_user),
                    db: sqlite3.Connection = Depends(get_db)):
    """설교 대본을 (다시) 받아쓴다. 기획할 때 자동으로 만들지만, 실패했거나 예전 기록이면 여기서 만든다."""
    row = db.execute('SELECT video_id, start_sec, end_sec FROM shorts WHERE id = ?', (shorts_id,)).fetchone()
    if not row:
        raise HTTPException(404, '기록을 찾을 수 없어요.')
    if not claim_transcript(db, row['video_id'], row['start_sec'], row['end_sec'], force=True):
        raise HTTPException(409, '이미 설교 대본을 만들고 있어요.')
    request.app.state.jobs.start_transcript(row['video_id'], row['start_sec'], row['end_sec'])
    return {'transcriptStatus': 'running'}


@router.delete('/shorts/{shorts_id}')
def delete_shorts(shorts_id: int, user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    row = db.execute('SELECT created_by FROM shorts WHERE id = ?', (shorts_id,)).fetchone()
    if not row:
        raise HTTPException(404, '기록을 찾을 수 없어요.')
    if user['role'] != 'admin' and row['created_by'] != user['email']:
        raise HTTPException(403, '본인이 만든 기록만 삭제할 수 있어요.')
    with db:
        db.execute('DELETE FROM shorts WHERE id = ?', (shorts_id,))
    return {'ok': True}
