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


def mark_interrupted(conn) -> None:
    with conn:
        conn.execute(
            "UPDATE jobs SET status = 'error', error = '서버가 재시작되어 중단됐어요. 다시 시도해 주세요.', "
            "finished_at = ? WHERE status IN ('queued', 'running')",
            (now_ms(),),
        )


class JobRunner:
    def __init__(self, app: FastAPI):
        self.app = app
        self.tasks: set[asyncio.Task] = set()

    def start(self, job_id: str) -> None:
        task = asyncio.create_task(self._run(job_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def wait_all(self) -> None:
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def _run(self, job_id: str) -> None:
        settings = self.app.state.settings
        call: gemini.GeminiCall = self.app.state.gemini
        conn = connect(settings.db_path)
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
                    self._finish(conn, job_id, shorts_id=shorts_id)
            except gemini.GeminiError as e:
                self._finish(conn, job_id, error=str(e))
            except Exception:
                log.exception('job %s failed', job_id)
                self._finish(conn, job_id, error='작업 중 알 수 없는 오류가 났어요. 잠시 후 다시 시도해 주세요.')
        finally:
            conn.close()

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
