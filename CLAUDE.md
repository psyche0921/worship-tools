# worship-tools

교회 예배팀이 매주 반복하는 작업 두 가지를 돕는 개인용 웹 도구입니다.

1. **세트리스트 → 유튜브 재생목록**: 카톡 단톡방으로 받은 세트리스트 메시지를 붙여넣으면, 유튜브 링크를 찾아 '일부 공개' 재생목록을 만들고 공유 링크를 복사할 수 있게 합니다.
2. **설교 쇼츠 기획**: 주일 예배 라이브 영상의 설교 구간을 Gemini API로 분석합니다. 사용자의 `SKILL.md`(예전 Gemini Gem 지침)에 따라 쇼츠 기획안을 만들고, PDF로 공유할 수 있게 합니다.

사용자는 한국어로 소통합니다. UI 문구, 커밋 메시지, 설명은 모두 한국어로 작성합니다.

**지금 진행할 작업은 `docs/handoff.md`에 있습니다. 먼저 읽어주세요.** 현재 구조와 결정 사항, 다음 단계(자체 서버 배포와 DB 이전), 정해지지 않은 질문을 정리해 두었습니다.

**v2 설계는 `docs/v2-design.md`에 확정되어 있습니다.** 배포 대상은 170번 서버(`tstech@192.168.0.170`), 주소는 `https://bbc.ppsoft.kr`입니다. 팀원은 쇼츠 기획만 쓰고, 세트리스트는 관리자만 씁니다.

## v2 구조 (`v2` 브랜치, bbc.ppsoft.kr)

- `server/app/`: FastAPI + SQLite. `auth.py`(Google ID 토큰 → 세션, `require_admin`), `jobs.py`(Gemini 백그라운드 작업), `gemini.py`(프롬프트, v1 문구 그대로), `routes/shorts.py`(팀원도 사용), `routes/records.py`(작업 기록 보기, 팀원도 사용), `routes/admin.py`(관리자 전용).
- `web/index.html`: v2 화면. v1을 바탕으로 저장소만 API로 바꿈. 파서·`cleanText`·PDF 코드를 고치면 v1 `index.html`과 차이가 생기니 v2 쪽을 기준으로 삼습니다.
- 작업 기록은 탭 하나(`작업 기록`)에 모았습니다. 세트리스트/쇼츠 기획 탭에는 기록 목록이 없습니다. **언제나 주일 예배 기준으로 묶어서** 10주씩 보여 줍니다(묶기를 끄는 선택지는 없습니다). 기록이 어느 주일 것인지는 제목의 6자리 날짜로 정합니다(없으면 세트리스트는 돌아오는 일요일, 쇼츠는 지난 일요일).
- 검색은 날짜와 키워드(곡 제목, 설교 주제, 쇼츠 본문, 설교 대본)를 찾고, **맞은 기록이 있는 주를 통째로** 돌려줍니다. 찬양 제목으로 찾으면 그 주에 기획한 쇼츠도 함께 나옵니다.
- 설교 대본은 **영상 하나에 하나**입니다(`transcripts` 표, 키는 `video_id`). 같은 영상으로 기획을 두 번 해도 대본은 다시 만들지 않고 두 기록이 같은 대본을 봅니다. 쇼츠 기획이 끝나면 서버가 이어서 같은 구간을 받아씁니다(기획 결과를 먼저 보여 주려고 작업이 끝난 뒤에 돌립니다). 상태는 `transcripts.status`(''/running/done/error)로 두고, 화면의 '설교 대본 보기'에서 보거나 없으면 다시 만듭니다(`POST /api/shorts/{id}/transcript`). 대본의 `[h:mm:ss]`도 쇼츠 결과와 똑같이 영상 링크가 됩니다.
- 권한은 반드시 서버 의존성(`current_user`, `require_admin`)으로 막습니다. 화면에서 숨기는 것만으로는 안 됩니다.
- 배포: 170번 서버 `~/worship-tools`에서 `deploy/deploy.sh`(git pull → docker compose). 설정 값은 서버의 `deploy/.env`(git 제외). nginx 설정과 certbot은 sudo가 필요해서 사용자가 실행합니다.

## 현재 구조 (v1, 정적 페이지)

- `index.html` 파일 하나에 HTML, CSS, JS가 모두 들어 있고, 빌드 단계는 없습니다. 현재 GitHub Pages(`https://psyche0921.github.io/worship-tools/`)로 배포되어 있습니다.
- `<script id="parser">` 블록은 Node에서도 `require`할 수 있습니다. 메시지 파싱 함수 `parseMessage`, `extractVideoId`, `nextSundayYYMMDD`, `buildDescription`이 이 블록에 있습니다.
- `vendor/jspdf.umd.min.js`(jsPDF 2.5.2)와 `fonts/NanumGothic-*.ttf`는 브라우저에서 PDF를 만들 때만 지연 로딩합니다.
- 모든 상태는 브라우저 `localStorage`에 저장합니다. 키 목록은 `docs/handoff.md`를 참고하세요.

## 꼭 지켜야 할 동작

- 재생목록은 항상 `privacyStatus: 'unlisted'`(링크가 있는 사람만 볼 수 있음)로 만듭니다.
- 세트리스트 제목은 메시지 속 6자리 날짜(`Setlist 261118`, `261011 Setlist` 둘 다 인식)로 정합니다. 날짜가 없으면 돌아오는 일요일 날짜를 씁니다(`Setlist YYMMDD`).
- 메시지는 `-----` 구분선으로 여러 세트리스트로 나뉩니다. `#`으로 시작하는 줄은 무시합니다. 곡 제목은 링크 바로 윗줄이고, 링크 아래 줄들(KEY/TEMPO/곡 구성)은 메모로 재생목록 설명에 넣습니다.
- 쇼츠 결과의 `(#t=0h38m54s)`, `[0:40:12](#t=...)`, `#t=...` 같은 앵커 표기는 화면, PDF, 복사 텍스트 어디에도 나오면 안 됩니다(`cleanText`). 모든 `h:mm:ss`/`m:ss` 타임스탬프는 `https://youtu.be/{id}?t={초}` 링크가 되어야 하고, PDF에서도 눌러서 이동할 수 있어야 합니다.
- 쇼츠 타임스탬프는 원본 라이브 영상 기준 절대 시각입니다. 모델이 잘라낸 구간 기준으로 답하면(모든 시각이 시작 시각보다 작으면) 시작 시각만큼 보정합니다.
- Gemini의 YouTube URL 입력은 **공개 영상만** 지원합니다. 일부 공개 영상은 분석하지 못합니다. 설교 구간은 `video_metadata.start_offset`/`end_offset`으로 잘라서 보냅니다.
- 이름 규칙: 쇼츠 기획 제목은 `YYMMDD 쇼츠 기획 · 설교 제목`, PDF 파일명은 `쇼츠기획_YYMMDD.pdf`입니다. 둘 다 해당 주 일요일 날짜를 씁니다.

## 테스트

```bash
pip install playwright pypdf   # Chromium 필요
python3 tests/e2e.py           # 정적 서버를 띄우고 Gemini/YouTube/Google 로그인은 가짜 응답으로 대체
```

v2 테스트(`server/`, `web/`):

```bash
python3 -m venv .venv && .venv/Scripts/python -m pip install -r server/requirements-dev.txt
.venv/Scripts/python -m pytest -q   # Google ID 토큰 검증은 tests/conftest.py의 가짜로 대체
.venv/Scripts/python -m pip install playwright pypdf && .venv/Scripts/python -m playwright install chromium
.venv/Scripts/python tests/e2e_v2.py   # 실제 서버를 띄우고 로그인·Gemini·YouTube는 가짜로 대체
```

`tests/fixtures/`에는 실제 단톡방 메시지 형식의 샘플과, `#t=` 표기가 섞인 쇼츠 응답 샘플이 있습니다.
