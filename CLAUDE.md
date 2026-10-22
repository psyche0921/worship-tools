# worship-tools

교회 예배팀이 매주 반복하는 작업 두 가지를 돕는 개인용 웹 도구입니다.

1. **세트리스트 → 유튜브 재생목록**: 카톡 단톡방으로 받은 세트리스트 메시지를 붙여넣으면, 유튜브 링크를 찾아 '일부 공개' 재생목록을 만들고 공유 링크를 복사할 수 있게 합니다.
2. **설교 쇼츠 기획**: 주일 예배 라이브 영상의 설교 구간을 Gemini API로 분석합니다. 사용자의 `SKILL.md`(예전 Gemini Gem 지침)에 따라 쇼츠 기획안을 만들고, PDF로 공유할 수 있게 합니다.

사용자는 한국어로 소통합니다. UI 문구, 커밋 메시지, 설명은 모두 한국어로 작성합니다.

**지금 진행할 작업은 `docs/handoff.md`에 있습니다. 먼저 읽어주세요.** 현재 구조와 결정 사항, 다음 단계(자체 서버 배포와 DB 이전), 정해지지 않은 질문을 정리해 두었습니다.

**v2 설계는 `docs/v2-design.md`에 확정되어 있습니다.** 배포 대상은 170번 서버(`tstech@192.168.0.170`), 주소는 `https://bbc.ppsoft.kr`입니다. 팀원은 쇼츠 기획만 쓰고, 세트리스트는 관리자만 씁니다.

**악보 관리(세트리스트 ↔ 구글 드라이브) 설계는 `docs/sheets-design.md`에 있고 구현했습니다. 그 악보를 drdr 채보 학습의 정답지로 쓰는 설계는 `docs/drdr-training.md`에 있고, 무거운 쪽은 drdr 저장소 작업입니다.**

## v2 구조 (`v2` 브랜치, bbc.ppsoft.kr)

- `server/app/`: FastAPI + SQLite. `auth.py`(Google ID 토큰 → 세션, `require_admin`), `jobs.py`(Gemini 백그라운드 작업), `gemini.py`(프롬프트, v1 문구 그대로), `routes/shorts.py`(팀원도 사용), `routes/records.py`(작업 기록 보기, 팀원도 사용), `routes/admin.py`(관리자 전용).
- `web/index.html`: v2 화면. v1을 바탕으로 저장소만 API로 바꿈. 파서·`cleanText`·PDF 코드를 고치면 v1 `index.html`과 차이가 생기니 v2 쪽을 기준으로 삼습니다.
- 작업 기록은 탭 하나(`작업 기록`)에 모았습니다. 한 주가 카드 하나(`.wk`)로 보이고, 머리에 `2026년 10월 18일 주일`과 그 주에 뭐가 몇 건인지(`세트리스트 1 · 쇼츠 기획 3`)를 적습니다. 세트리스트/쇼츠 기획 탭에는 기록 목록이 없습니다. **언제나 주일 예배 기준으로 묶어서** 10주씩 보여 줍니다(묶기를 끄는 선택지는 없습니다). 기록이 어느 주일 것인지는 제목의 6자리 날짜로 정합니다(없으면 세트리스트는 돌아오는 일요일, 쇼츠는 지난 일요일).
- 검색은 날짜와 키워드(곡 제목, 곡 메모, 설교 주제, 추가 요청, 쇼츠 본문, 설교 대본)를 찾고, **맞은 기록이 있는 주를 통째로** 돌려줍니다. 찬양 제목으로 찾으면 그 주에 기획한 쇼츠도 함께 나옵니다.
- 왜 걸렸는지가 중요해서, 응답의 `matches`에 조각마다 어느 칸에서 맞았는지와 주변 문구를 담고 화면은 검색칸 아래 '검색된 이유' 상자로 보여 줍니다.
- 설교 대본은 **영상 하나에 하나**입니다(`transcripts` 표, 키는 `video_id`). 같은 영상으로 기획을 두 번 해도 대본은 다시 만들지 않고 두 기록이 같은 대본을 봅니다. 쇼츠 기획이 끝나면 서버가 이어서 같은 구간을 받아씁니다(기획 결과를 먼저 보여 주려고 작업이 끝난 뒤에 돌립니다). 상태는 `transcripts.status`(''/running/done/error)로 두고, 화면의 '설교 대본 보기'에서 보거나 없으면 다시 만듭니다(`POST /api/shorts/{id}/transcript`). 대본의 `[h:mm:ss]`도 쇼츠 결과와 똑같이 영상 링크가 됩니다.
- 권한은 반드시 서버 의존성(`current_user`, `require_admin`)으로 막습니다. 화면에서 숨기는 것만으로는 안 됩니다.
- 배포: 170번 서버 `~/worship-tools`에서 `deploy/deploy.sh`(git pull → docker compose). 설정 값은 서버의 `deploy/.env`(git 제외). nginx 설정과 certbot은 sudo가 필요해서 사용자가 실행합니다.

## 사이트 아이콘과 '앱으로 설치' (v2)

- 아이콘은 `drdr.ppsoft.kr`과 같은 톤입니다. 주황(`#d4652a`) 둥근 사각 박스(반지름 25%) 안에 흰 `BBC` 글자.
- `tools/make-icons.py`로 다시 만듭니다(`pip install pillow fonttools`). `fonts/NanumGothic-Bold.ttf`(OFL)의 글리프 외곽선을 그대로 써서 SVG와 PNG가 같은 모양입니다. **아이콘 파일을 손으로 고치지 말고 이 스크립트를 고쳐서 다시 만드세요.**
- 만들어지는 파일(`web/`): `icon.svg`, `icon-192.png`, `icon-512.png`, `icon-maskable-512.png`(안드로이드가 잘라내도 되게 테두리 없이 여유를 둠), `apple-touch-icon.png`(아이폰이 직접 둥글게 깎으니 모서리를 깎지 않음), `favicon.ico`.
- 스마트폰에서 '앱 설치'가 뜨려면 `web/manifest.webmanifest`와 `web/sw.js`(서비스 워커)가 둘 다 있어야 합니다. 서비스 워커는 **네트워크 우선**이라 배포하면 바로 새 화면이 보입니다. `/api/`와 `/auth/`는 캐시하지 않습니다.
- `.webmanifest` 타입은 `server/app/main.py`에서 `mimetypes.add_type`으로 등록합니다(파이썬 기본 목록에 없어서 빼면 브라우저가 설치 설정을 읽지 않습니다).
- 설치 단추(`#installBtn`)는 `beforeinstallprompt`가 올 때만 보입니다(안드로이드 크롬). 아이폰은 사파리 '공유 > 홈 화면에 추가'입니다.

## 악보 (v2)

- **서버는 구글 드라이브를 부르지 않습니다.** OAuth 동의 화면이 '테스트' 상태라 refresh token이 7일 뒤 만료되기 때문입니다. 관리자 브라우저가 드라이브를 읽고 쓰고(`drive.file` 범위, Picker로 고른 악보 폴더), 서버는 **악보 사본(`/data/sheets`)과 곡↔악보 매핑만** 보관합니다. 새 드라이브 기능을 넣을 때도 이 선을 지키세요.
- 곡 키는 `app/sheets.py`의 `song_key()`입니다. 괄호 안·확장자를 떼고 공백과 기호를 모두 지웁니다. 그래서 `새 힘 얻으리`와 `새힘얻으리`가 같은 곡이 됩니다.
- **메들리는 곡마다 악보가 하나입니다.** `split_songs()`가 `&`, `+`, ` / `로 쪼갭니다(`나로부터 & 기대` → 2곡). 조각이 2글자보다 짧으면 쪼개지 않습니다(`R&B`).
- 파일 이름에 규칙이 없으니 ① 이미 매핑된 곡 ② 드라이브 목록 캐시(`drive_index`)에서 이름에 곡 키가 **들어 있는** 파일 ③ `difflib` 유사도 상위 5개 순서로 찾습니다. 사람이 고르면 그 파일 이름을 `sheet_aliases`에 남겨 다음에도 걸립니다.
- 기록을 읽을 때 저장된 `sheetId`가 없으면 **곡 제목으로 지금 매핑을 찾습니다**(`resolve_songs`, `auto: true`). 그래서 악보를 나중에 올려도 그 곡이 나왔던 지난 주 기록에 바로 보입니다.
- 팀원도 악보를 받고 올릴 수 있습니다. 팀원이 올린 것은 서버에만 두고(`sync='pending'`) 관리자가 설정 탭에서 드라이브로 보냅니다. **드라이브 연결(`driveFileId`)은 관리자만** 할 수 있습니다(서버에서 검사).
- 악보를 지워도 **드라이브 원본은 지우지 않습니다**. 매핑과 서버 사본만 지웁니다.
- 올릴 수 있는 형식은 앞머리 바이트로 가립니다(PDF·PNG·JPEG). 클라이언트가 말한 타입을 믿지 않습니다.
- Picker에는 두 값이 필요합니다. `GOOGLE_PICKER_KEY`(브라우저 API 키, 리퍼러에 `https://docs.google.com/*`도 넣어야 함 — Picker가 그 iframe에서 그려짐)와 `GOOGLE_PICKER_APP_ID`(Cloud 프로젝트 번호, `setAppId`). **`drive.file` 범위에서는 appId가 없으면 고른 폴더가 앱에 허락되지 않습니다.** 둘 다 관리자만 보는 `/api/settings`로 내려주고, 공개인 `/api/config`에 넣지 마세요.
- `tools/check-sheets.py`는 악보 PDF가 프로그램 PDF인지 스캔인지 세어 봅니다(drdr 학습 1단계 비용 가늠용, `pip install pymupdf`).

## 자동 쇼츠 기획 (v2)

- 매주 **월요일 오후 1시(KST)**, `app/auto.py`의 `AutoRunner`가 교회 유튜브 채널에서 그 주일 예배 영상을 찾아 설교 구간 찾기 → 쇼츠 기획 → 대본까지 돌립니다. 영상이 아직 없으면 저녁 6시까지 30분마다 다시 봅니다(5분마다 깨어나 `due()`로 판단하므로 서버를 껐다 켜도 그 주 안에서 따라잡습니다).
- 영상은 **제목에 든 날짜**로 고릅니다(`2026.10.18` 같은 표기). 날짜가 맞는 영상이 없으면 '아직 안 올라옴'으로 보고 다시 시도합니다.
- 찾는 방법은 두 가지입니다(`app/youtube.py`). `YOUTUBE_API_KEY`가 있으면 Data API(업로드 재생목록)를 먼저 쓰고, 없거나 실패하면 채널 `/streams` 페이지의 `ytInitialData`를 읽습니다. 페이지 구조(`lockupViewModel`)는 유튜브가 바꾸면 깨질 수 있으니 테스트의 샘플을 함께 고치세요.
- 자동으로 만든 기록은 `auto@worship.local`(이름 '자동') 계정이 만든 것으로 남습니다. 로그인할 수 없는 주소이고 팀원 명단에서는 숨깁니다.
- 켜기·끄기와 채널 주소, 마지막 실행 결과는 설정 탭에 있습니다(`auto_enabled`, `yt_channel`, `auto_last` 설정 값).
- '지금 한 번 실행'(`POST /api/auto/run`)은 **바로 돌려주고 백그라운드로 돕니다**. 한 요청 안에서 끝내려 하면 2~4분이 걸려 nginx가 504를 냅니다. 진행 상황은 `auto_last`의 `running`으로 두고 화면이 5초마다 `GET /api/settings`로 확인합니다. 오래 걸리는 것을 새로 만들 때도 같은 방식을 쓰세요.

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
