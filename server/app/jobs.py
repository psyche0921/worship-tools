"""Gemini 작업 실행기.

쇼츠 기획은 1~2분 걸려서, 요청은 작업으로 등록하고 바로 돌려준다. 실행은 같은 프로세스의
asyncio 작업으로 하고(워커 1개 전제), 브라우저는 GET /api/jobs/{id}로 결과를 확인한다.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI

from . import gemini
from .db import connect, get_setting, now_ms

log = logging.getLogger('worship.jobs')

KST = timezone(timedelta(hours=9))  # 한국은 서머타임이 없다


def kst_midnight_ms(at_ms: int) -> int:
    d = datetime.fromtimestamp(at_ms / 1000, KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp() * 1000)


def shorts_title(at_ms: int, sermon: str) -> str:
    """`YYMMDD 쇼츠 기획 · 설교 제목`. 날짜는 그 주 일요일(v1의 sundayOf와 같음)."""
    d = datetime.fromtimestamp(at_ms / 1000, KST).date()
    d -= timedelta(days=(d.weekday() + 1) % 7)
    return f'{d:%y%m%d} 쇼츠 기획' + (f' · {sermon}' if sermon else '')


INTERRUPTED = '서버가 재시작되어 중단됐어요. 다시 시도해 주세요.'


def mark_interrupted(conn) -> None:
    with conn:
        conn.execute(
            "UPDATE jobs SET status = 'error', error = ?, finished_at = ? "
            "WHERE status IN ('queued', 'running')",
            (INTERRUPTED, now_ms()),
        )
        conn.execute("UPDATE transcripts SET status = 'error', error = ? WHERE status = 'running'",
                     (INTERRUPTED,))


def claim_transcript(conn, video_id: str, start: int, end: int, force: bool = False) -> bool:
    """대본 받아쓰기를 맡는다(영상 하나에 하나). 이미 있거나 만드는 중이면 False.

    force=True는 '다시 만들기'다. 만드는 중일 때만 거절한다.
    """
    keep = "transcripts.status <> 'running'" if force else "transcripts.status = 'error'"
    with conn:
        cur = conn.execute(
            "INSERT INTO transcripts (video_id, start_sec, end_sec, status, updated_at) "
            "VALUES (?, ?, ?, 'running', ?) "
            "ON CONFLICT(video_id) DO UPDATE SET status = 'running', error = NULL, "
            'start_sec = excluded.start_sec, end_sec = excluded.end_sec, updated_at = excluded.updated_at '
            f'WHERE {keep}',
            (video_id, start or 0, end or 0, now_ms()),
        )
    return cur.rowcount > 0


class JobRunner:
    def __init__(self, app: FastAPI):
        self.app = app
        self.tasks: set[asyncio.Task] = set()

    def start(self, job_id: str) -> None:
        self._spawn(self._run(job_id))

    def start_transcript(self, video_id: str, start: int, end: int) -> None:
        """이미 claim_transcript로 맡아 둔 대본을 받아쓴다."""
        self._spawn(self._run_transcript(video_id, start, end))

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def wait_all(self) -> None:
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def _run(self, job_id: str) -> None:
        settings = self.app.state.settings
        call: gemini.GeminiCall = self.app.state.gemini
        conn = connect(settings.db_path)
        shorts_id, claimed = None, False
        try:
            row = conn.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
            inp = json.loads(row['input_json'])
            with conn:
                conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job_id,))
            model = get_setting(conn, 'model') or settings.default_model
            try:
                if row['kind'] == 'detect':
                    text = await call(model, gemini.detect_body(inp['videoId']))
                    self._finish(conn, job_id, result=gemini.parse_detect(text))
                else:
                    skill = get_setting(conn, 'skill_md') or ''
                    body = gemini.plan_body(skill, inp['videoId'], inp.get('start'), inp.get('end'), inp.get('extra', ''))
                    text = await call(model, body)
                    shorts_id = self._save_shorts(conn, row, inp, text, model)
                    # 같은 영상의 대본이 이미 있으면 다시 만들지 않는다. 화면이 바로 '만드는 중'으로
                    # 보이도록 작업을 끝내기 전에 맡아 둔다.
                    claimed = claim_transcript(conn, inp['videoId'], inp.get('start') or 0, inp.get('end') or 0)
                    self._finish(conn, job_id, shorts_id=shorts_id)
            except gemini.GeminiError as e:
                self._finish(conn, job_id, error=str(e))
            except Exception:
                log.exception('job %s failed', job_id)
                self._finish(conn, job_id, error='작업 중 알 수 없는 오류가 났어요. 잠시 후 다시 시도해 주세요.')
            # 대본은 기획 결과를 먼저 보여 준 뒤에 이어서 받아쓴다(작업은 이미 끝난 상태).
            if claimed:
                await self._transcribe(conn, inp['videoId'], inp.get('start') or 0, inp.get('end') or 0)
        finally:
            conn.close()

    async def _run_transcript(self, video_id: str, start: int, end: int) -> None:
        conn = connect(self.app.state.settings.db_path)
        try:
            await self._transcribe(conn, video_id, start, end)
        finally:
            conn.close()

    async def _transcribe(self, conn, video_id: str, start: int, end: int) -> None:
        """설교 대본 받아쓰기. 부르기 전에 claim_transcript로 맡아 둔다."""
        settings = self.app.state.settings
        call: gemini.GeminiCall = self.app.state.gemini
        model = get_setting(conn, 'model') or settings.default_model
        text, error = '', None
        try:
            text = await call(model, gemini.transcript_body(video_id, start or None, end or None))
        except asyncio.CancelledError:
            self._save_transcript(conn, video_id, '', INTERRUPTED, model)
            raise
        except gemini.GeminiError as e:
            error = str(e)
        except Exception:
            log.exception('transcript for %s failed', video_id)
            error = '설교 대본을 만들다 알 수 없는 오류가 났어요. 잠시 후 다시 시도해 주세요.'
        self._save_transcript(conn, video_id, text, error, model)

    @staticmethod
    def _save_transcript(conn, video_id: str, text: str, error: str | None, model: str) -> None:
        with conn:
            conn.execute(
                'UPDATE transcripts SET text = ?, status = ?, error = ?, model = ?, updated_at = ? '
                'WHERE video_id = ?',
                ('' if error else text, 'error' if error else 'done', error, model, now_ms(), video_id),
            )

    @staticmethod
    def _save_shorts(conn, row, inp: dict, text: str, model: str) -> int:
        at = now_ms()
        sermon = (inp.get('sermonTitle') or '').strip()
        with conn:
            cur = conn.execute(
                'INSERT INTO shorts (video_id, start_sec, end_sec, title, sermon_title, extra, text, model, '
                'created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (inp['videoId'], inp.get('start') or 0, inp.get('end') or 0, shorts_title(at, sermon),
                 sermon, inp.get('extra', ''), text, model, row['created_by'], at),
            )
        return cur.lastrowid

    @staticmethod
    def _finish(conn, job_id: str, result: dict | None = None, shorts_id: int | None = None,
                error: str | None = None) -> None:
        with conn:
            conn.execute(
                'UPDATE jobs SET status = ?, result_json = ?, shorts_id = ?, error = ?, finished_at = ? WHERE id = ?',
                ('error' if error else 'done', json.dumps(result, ensure_ascii=False) if result else None,
                 shorts_id, error, now_ms(), job_id),
            )
