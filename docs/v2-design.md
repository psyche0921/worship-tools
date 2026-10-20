# v2 설계: bbc.ppsoft.kr (자체 서버 + DB)

`docs/handoff.md`의 v2 목표를 170번 서버 기준으로 구체화한 문서입니다. 2026-10-19 사용자와 정한 내용을 반영했습니다.

## 1. 확정된 결정

| 항목 | 결정 |
|---|---|
| 주소 | `https://bbc.ppsoft.kr` (DNS는 이미 서버 공인 IP `14.50.236.172`를 가리킴) |
| 서버 | 170번 서버(`192.168.0.170`, CentOS Stream 10, 계정 `tstech`). 다른 서비스와 함께 쓰는 서버 |
| 사용자 | 관리자 1명(사용자 본인) + 예배팀원 |
| 로그인 | Google 로그인 + 관리자가 등록한 이메일만 허용 |
| 역할 | **관리자**: 모든 기능. **팀원**: 쇼츠 기획만 |
| 쇼츠 기록 | 팀 공용. 모두가 모든 기록을 보고, 삭제는 본인 것만(관리자는 전체) |
| 세트리스트 | 관리자만. 재생목록은 관리자 YouTube 계정에 만들고 링크만 공유 |
| Gemini 키 | 서버 환경변수로 옮김. 브라우저에는 키가 없음 |
| DB | SQLite (소규모, 별도 DB 서버 불필요) |

### 역할별 권한

| 기능 | 관리자 | 팀원 |
|---|---|---|
| 세트리스트 → 재생목록 만들기 | ✅ | ❌ (메뉴 없음, API도 403) |
| 쇼츠 기획 생성, 설교 구간 찾기, PDF 공유 | ✅ | ✅ |
| 쇼츠 기록 보기 | ✅ 전체 | ✅ 전체 |
| 쇼츠 기록 삭제 | ✅ 전체 | 본인이 만든 것만 |
| 작업 기록 보기(세트리스트 + 쇼츠, 검색·주일 묶음) | ✅ | ✅ |
| 세트리스트 기록 삭제 | ✅ | ❌ |
| 설정 (SKILL.md, 모델) | ✅ | ❌ |
| 팀원 관리 (허용 이메일) | ✅ | ❌ |

**권한은 서버에서 확인합니다.** 화면에서 메뉴를 숨기는 것은 편의일 뿐이고, 모든 API가 세션의 역할을 직접 검사합니다.

## 2. 서버 현황 (2026-10-19 확인)

- 자원: 16코어, RAM 31GB, `/` 여유 28GB, `/home` 여유 17GB(사용률 90%). 이 앱은 데이터가 작아서 문제없지만 `/home`은 여유가 적다는 점을 기억해 둡니다.
- 이미 돌고 있는 것: nginx 1.26(서브도메인별 `conf.d/*.conf` + certbot), Docker 29 + Compose v5(drdr), pm2(pedal), uvicorn(gp5, tone), MariaDB.
- `tstech`는 `docker` 그룹이라 sudo 없이 컨테이너를 올릴 수 있습니다. **sudo는 비밀번호가 필요**해서 nginx 설정과 인증서 발급은 사용자가 직접 실행합니다.
- SELinux Enforcing, `httpd_can_network_connect` on(nginx → 로컬 포트 프록시 가능). firewalld 사용 중.
- 사용 중인 로컬 포트: 3000, 3100, 3306, 8000, 8020, 8810, 9090 등. **이 앱은 `127.0.0.1:8830`** 을 씁니다(확인 시점에 비어 있음).

## 3. 전체 구조

```
브라우저 (폰/PC)
   │  https://bbc.ppsoft.kr
   ▼
nginx (TLS, certbot) ── /etc/nginx/conf.d/bbc.ppsoft.kr.conf
   │  proxy_pass http://127.0.0.1:8830
   ▼
Docker 컨테이너 worship-web (FastAPI + uvicorn, 워커 1개)
   ├─ 정적 파일: web/index.html, vendor/, fonts/
   ├─ /api/*  : 로그인, 쇼츠, 세트리스트 기록, 설정, 팀원
   ├─ 작업 실행기: Gemini 호출을 백그라운드로 실행
   └─ SQLite: /data/worship.db  (호스트 ~/worship-tools/data 에 마운트)
         │
         └─▶ Gemini API (서버 키)

YouTube Data API는 지금처럼 관리자 브라우저에서 직접 호출합니다(서버를 거치지 않음).
```

### 기술 선택과 이유

- **FastAPI + uvicorn (Python 3.12)**: 서버에 이미 같은 방식의 앱들이 있고, Gemini 호출과 Google ID 토큰 검증에 쓸 라이브러리가 충분합니다.
- **Docker Compose**: sudo 없이 배포하고, 재부팅 뒤 자동으로 다시 뜨게 할 수 있습니다(`restart: unless-stopped`). 같은 서버의 다른 앱과 Python 환경이 섞이지 않습니다.
- **워커 1개**: SQLite 쓰기 경합이 없고, 작업 실행기를 같은 프로세스 안에 둘 수 있습니다. 예배팀 규모에서는 충분합니다.
- **빌드 단계 없는 프론트엔드 유지**: v1처럼 HTML 파일 하나에 두고, 파서·PDF·`cleanText` 코드는 그대로 씁니다. 바뀌는 것은 저장소(`localStorage` → API)와 로그인, 역할별 화면입니다.

## 4. 로그인과 세션

1. 로그인하지 않은 사용자에게는 로그인 화면만 보여 줍니다. "Google로 로그인" 버튼은 Google Identity Services의 ID 토큰 방식입니다(범위는 `openid email profile`뿐).
2. 브라우저가 받은 ID 토큰을 `POST /api/auth/google`로 보냅니다.
3. 서버는 `google-auth`로 토큰 서명, `aud`(OAuth 클라이언트 ID), `email_verified`를 검증합니다. 그다음 `users` 표에 있는 이메일인지 확인합니다. 없으면 "등록되지 않은 계정" 안내와 함께 403을 돌려줍니다.
4. 통과하면 서버 세션을 만들고 쿠키를 보냅니다: `HttpOnly; Secure; SameSite=Lax; Max-Age=30일`. 쿠키에는 임의 토큰만 넣고, DB에는 그 해시를 저장합니다.
5. 상태를 바꾸는 요청(POST/PUT/DELETE)은 `Origin` 헤더가 `https://bbc.ppsoft.kr`인지 확인합니다(CSRF 방지).

- **관리자 지정**: 서버 환경변수 `ADMIN_EMAILS`. 시작할 때 이 이메일을 `role=admin`으로 넣습니다. 공개 저장소라 이메일은 코드에 넣지 않습니다.
- **YouTube 권한**은 별개입니다. 관리자가 재생목록을 만들 때만 v1처럼 토큰 클라이언트(`youtube` 범위)로 따로 받습니다. 팀원은 이 창을 볼 일이 없습니다.
- **OAuth 동의 화면 '테스트' 상태**: 테스트 상태에서는 테스트 사용자로 등록한 계정만 로그인할 수 있다고 보고, 팀원 이메일을 Google Cloud 콘솔의 테스트 사용자에도 추가합니다(최대 100명). 기본 범위만 쓰는 로그인도 이 제한을 받는지는 구현할 때 실제로 확인합니다.

## 5. 쇼츠 생성: 백그라운드 작업 + 폴링

쇼츠 기획은 1~2분 걸립니다. 폰 화면이 꺼지거나 브라우저가 백그라운드로 가면 오래 걸리는 요청은 끊기기 쉽습니다. 그래서 요청을 작업으로 바꿔 처리합니다.

1. `POST /api/jobs` `{kind: "plan", videoId, start, end, extra, sermonTitle}` → `{jobId}`를 바로 돌려줍니다.
2. 서버가 백그라운드에서 Gemini를 호출합니다. 성공하면 결과를 `shorts` 표에 저장하고 작업에 `shortsId`를 기록합니다.
3. 브라우저는 `GET /api/jobs/{id}`를 3초마다 확인합니다. 화면을 닫았다 열어도 작업 기록에 결과가 나타납니다.
4. "설교 구간 찾기"(`kind: "detect"`)도 같은 방식으로 처리합니다. 결과 `{start, end, title}`은 작업 결과에만 남기고 기록에는 넣지 않습니다.

- 서버가 재시작되면 `running` 상태였던 작업은 `error`("서버가 재시작되어 중단됨")로 바꿉니다.
- **사용량 제한**: 키가 서버에 하나뿐이라 사용자별로 하루 `plan` 작업 수를 제한합니다(기본 20회, 환경변수로 조정). 같은 사용자의 동시 작업은 1개까지만 허용합니다.
- 프롬프트 구성(설교 구간 안내, 절대 시각 지시, 추가 요청)은 v1 문구를 서버로 옮깁니다. SKILL.md는 DB 설정에서 읽고 frontmatter를 제거합니다.
- **설교 대본**: 기획이 끝나 작업을 `done`으로 바꾼 뒤, 같은 백그라운드 작업에서 같은 구간을 한 번 더 호출해 받아쓰고 `transcripts` 표에 넣습니다(기획 결과를 먼저 보여 주기 위해서). 대본은 영상 하나에 하나라서, 그 영상의 대본이 이미 있거나 만드는 중이면 건너뜁니다(`claim_transcript`가 한 번만 맡도록 보장). 진행 상태는 `transcripts.status`(''/running/done/error)에 두고, 서버가 재시작되면 `running`은 `error`로 바꿉니다.
- 서버는 Gemini 응답 원문을 그대로 저장합니다. `#t=` 제거, 타임스탬프 링크, 시작 시각 보정은 v1처럼 화면에 그릴 때 처리합니다(같은 원문을 나중에 다시 그릴 수 있도록).

## 6. DB 스키마 (SQLite, WAL 모드)

```sql
users    (email TEXT PK, name TEXT, role TEXT CHECK(role IN ('admin','member')),
          created_at INT, last_login_at INT)
sessions (token_hash TEXT PK, email TEXT REFERENCES users ON DELETE CASCADE,
          created_at INT, expires_at INT)
settings (key TEXT PK, value TEXT, updated_at INT, updated_by TEXT)
          -- 'skill_md', 'model'
shorts   (id INTEGER PK, video_id TEXT, start_sec INT, end_sec INT,
          title TEXT, sermon_title TEXT, extra TEXT, text TEXT, model TEXT,
          created_by TEXT, created_at INT, legacy_key TEXT UNIQUE)
transcripts (video_id TEXT PK, start_sec INT, end_sec INT, text TEXT,
          status TEXT CHECK(status IN ('','running','done','error')), error TEXT,
          model TEXT, updated_at INT)
setlists (id INTEGER PK, playlist_id TEXT UNIQUE, title TEXT, url TEXT,
          ok INT, total INT, songs_json TEXT, raw_message TEXT,
          created_by TEXT, created_at INT)
jobs     (id TEXT PK, kind TEXT, status TEXT, input_json TEXT, result_json TEXT,
          error TEXT, shorts_id INT, created_by TEXT, created_at INT, finished_at INT)
```

- 시각은 모두 밀리초 epoch입니다(v1의 `at`과 같음).
- `songs_json`은 v1 형식 그대로입니다: `[{id, name, notes[]}]`.
- `raw_message`: 세트리스트 원본 메시지입니다. 나중에 다시 파싱할 수 있게 저장합니다(v1에는 없던 항목).
- `transcripts`: **설교 대본은 영상 하나에 하나**입니다. 같은 영상으로 기획을 두 번 해도 대본은 한 벌만 만들고, 두 기록이 같은 대본을 봅니다(`shorts.video_id`로 이어 붙임).
- `legacy_key`: v1 기록을 가져올 때 쓰는 키(`s{at}`)입니다. 같은 파일을 두 번 가져와도 중복되지 않습니다.
- v1의 개수 제한(100/60)은 없애고, 목록 API에서 페이지를 나눠 돌려줍니다.

## 7. API

모든 응답은 JSON입니다. 로그인하지 않으면 401, 권한이 없으면 403을 돌려줍니다.

| 메서드 · 경로 | 권한 | 내용 |
|---|---|---|
| `GET /api/health` | 누구나 | 배포 확인용 |
| `GET /api/config` | 누구나 | `{clientId}`. 로그인 버튼을 그리는 데 필요 |
| `POST /api/auth/google` | 누구나 | `{credential}` → 세션 쿠키 |
| `POST /api/auth/logout` | 로그인 | 세션 삭제 |
| `GET /api/me` | 로그인 | `{email, name, role}` |
| `POST /api/jobs` | 로그인 | 쇼츠 기획 또는 설교 구간 찾기 작업 시작 |
| `GET /api/jobs/{id}` | 본인 작업 | 상태와 결과 |
| `GET /api/shorts?before=` | 로그인 | 목록(최신순, 20개씩). 원문 없이 메타만 |
| `GET /api/shorts/{id}` | 로그인 | 원문 포함 |
| `POST /api/shorts/{id}/transcript` | 로그인 | 그 기록의 **영상** 대본 다시 받아쓰기(기획할 때 자동으로 한 번 만든다). 이미 만들고 있으면 409 |
| `DELETE /api/shorts/{id}` | 만든 사람 · 관리자 | 삭제 |
| `GET /api/records?kind=&q=&page=&size=` | 로그인 | 작업 기록 목록. 언제나 주일별로 묶어서 10주씩. `q`는 날짜(261115, 2026-11-15)와 키워드(곡 제목, 설교 주제, 쇼츠 본문, 설교 대본)를 모두 찾고 조각은 AND. **맞은 기록이 있는 주를 통째로** 돌려줘서, 찬양 제목으로 찾으면 그 주의 쇼츠 기획도 함께 나온다. `matches`에 '왜 걸렸는지'(기록마다 조각별로 어느 칸에서 맞았는지와 주변 문구)를 같이 담는다 |
| `GET /api/records/setlist/{재생목록 ID}` | 로그인 | 세트리스트 상세(원본 메시지는 관리자에게만). 쇼츠 상세는 `/api/shorts/{id}` |
| `GET /api/setlists`, `GET /api/setlists/{재생목록 ID}` | 관리자 | 목록 / 상세(원본 메시지 포함) |
| `POST /api/setlists` | 관리자 | 재생목록을 만든 뒤 결과 저장(원본 메시지 포함) |
| `DELETE /api/setlists/{재생목록 ID}` | 관리자 | 기록 삭제(YouTube 재생목록은 지우지 않음) |
| `GET/PUT /api/settings` | 관리자 | SKILL.md, 모델, 자동 기획(켜기·채널·마지막 실행 결과) |
| `POST /api/auto/run` | 관리자 | 자동 기획을 지금 한 번 실행(월요일을 기다리지 않고) |
| `GET/POST/DELETE /api/members` | 관리자 | 허용 이메일 관리(관리자 자신은 삭제 불가) |

## 8. 화면 변경

- **처음 화면**: 로그인 전에는 로그인 카드만 보입니다. 등록되지 않은 계정이면 "관리자에게 이메일 등록을 요청하세요"라고 안내합니다.
- **탭**: 관리자에게는 `세트리스트 | 쇼츠 기획 | 작업 기록 | 설정`을, 팀원에게는 `쇼츠 기획 | 작업 기록`을 보여 줍니다.
- **검색된 이유**: 검색하면 검색칸 바로 아래 '검색된 이유' 상자에 맞은 기록만 모아서, 조각마다 어느 칸(제목·곡 제목·곡 메모·설교 주제·추가 요청·쇼츠 본문·설교 대본·날짜)에서 맞았는지와 그 주변 문구를 보여 줍니다(맞은 말은 표시). 한 조각이 여러 칸에서 맞으면 최대 3칸까지 모두 적습니다 — 제목에도 있고 설교 대본에도 있으면 둘 다 보여야 대본까지 찾았다는 걸 압니다. 같은 주라서 함께 나온 기록은 여기 넣지 않습니다. 줄을 누르면 그 기록이 열립니다.
- **작업 기록 탭**: 세트리스트와 쇼츠 기획 기록을 언제나 주일 예배 기준으로 묶어서 보여 줍니다(10주씩, 페이지 넘기기). 종류(전체/세트리스트/쇼츠)를 거르고, 날짜·키워드로 찾습니다. 찬양 제목으로 찾으면 그 주의 쇼츠 기획도 함께 나와서, 그 주에 무엇을 부르고 무엇을 설교했는지 같이 봅니다. 줄을 누르면 상세(세트리스트는 곡 목록과 재생목록 링크, 쇼츠는 본문·설교 대본·PDF)가 열리고, 권한이 있으면 거기서 지웁니다.
- **설교 대본**: 쇼츠 기획 화면과 작업 기록 상세에서 '설교 대본 보기'로 펼칩니다. 아직 만드는 중이면 그렇게 알려 주고 5초마다 다시 확인하며, 없거나 실패했으면 '설교 대본 만들기'로 다시 받아씁니다. 대본의 `[h:mm:ss]`도 영상 링크가 됩니다(PDF에는 넣지 않습니다).
- **설정**: OAuth 클라이언트 ID와 Gemini 키 입력칸은 없앱니다(서버 설정으로 옮김). 관리자 설정 탭에서는 SKILL.md 편집, 모델명, 팀원 이메일 추가·삭제를 할 수 있습니다.
- **쇼츠 기록**: 각 항목에 만든 사람 이름을 붙이고, 삭제 버튼은 권한이 있을 때만 보여 줍니다(작업 기록 탭의 상세에서).
- **세트리스트**: 파싱, 미리보기, 재생목록 생성은 그대로입니다. 생성이 끝나면 `POST /api/setlists`로 저장합니다.
- 그대로 두는 것: 파서 블록(`<script id="parser">`), `cleanText`, 타임스탬프 링크, 시작 시각 보정, jsPDF PDF와 공유창, 2단계 삭제 확인.
- `localStorage`에는 마지막 탭 같은 편의 정보만 남깁니다.

## 9. v1 기록 옮기기 (2026-10-20 취소)

예전 기록은 옮기지 않기로 했습니다. v2 관리자 설정의 **가져오기**(`POST /api/import`)는 뺐습니다. v2는 빈 DB로 시작하고, v1 기록은 GitHub Pages 쪽 `localStorage`에 그대로 남습니다.

- v1 페이지(`index.html`)의 "기록 내보내기" 버튼은 아직 남아 있지만 받아 줄 곳이 없습니다.
- `shorts.legacy_key` 칸은 스키마에 남아 있습니다(마이그레이션은 덧붙이기만 합니다). 이제 아무것도 쓰지 않습니다.

## 10. 저장소 구성

```
index.html            v1 그대로 유지(GitHub Pages)
web/index.html        v2 화면. vendor/, fonts/는 루트의 것을 같이 씀
server/
  app/main.py         FastAPI 앱, 정적 파일, 라우터 연결
  app/db.py           SQLite 연결, 스키마 생성·마이그레이션
  app/auth.py         ID 토큰 검증, 세션, 권한 의존성
  app/jobs.py         백그라운드 작업 실행기
  app/gemini.py       Gemini 호출, 프롬프트 구성
  app/routes/*.py     shorts, records(작업 기록 보기), setlists, settings, members
  requirements.txt
  Dockerfile
deploy/
  compose.yaml        127.0.0.1:8830:8000, ./data:/data, env_file .env
  bbc.ppsoft.kr.conf  nginx 설정 (설치 방법 주석 포함)
  deploy.sh           서버에서 git pull → docker compose up -d --build
  backup.sh           SQLite 백업 (cron)
  .env.example        GEMINI_API_KEY, GOOGLE_CLIENT_ID, ADMIN_EMAILS, ...
tests/
  e2e.py              v1 테스트 (그대로)
  test_api.py         pytest + FastAPI TestClient (Gemini, Google 토큰 검증은 가짜로)
  e2e_v2.py           Playwright: 관리자·팀원 화면, 작업 폴링, PDF
```

v2 화면을 `web/`에 따로 두는 이유: 루트 `index.html`을 v2로 바꾸면 GitHub Pages가 API 없이 뜨면서 깨집니다. 기록을 다 옮길 때까지 v1을 그대로 살려 둡니다.

## 11. 서버 설정 값 (`~/worship-tools/deploy/.env`, 권한 600, git에 넣지 않음)

| 변수 | 내용 |
|---|---|
| `GEMINI_API_KEY` | Gemini API 키 |
| `GOOGLE_CLIENT_ID` | OAuth 웹 클라이언트 ID(로그인과 YouTube 토큰에 같이 사용) |
| `ADMIN_EMAILS` | 관리자 이메일(쉼표로 구분) |
| `PUBLIC_ORIGIN` | `https://bbc.ppsoft.kr` (Origin 검사, 쿠키) |
| `DEFAULT_MODEL` | `gemini-3.8-flash` (설정 탭 값이 우선) |
| `YOUTUBE_API_KEY` | (선택) 자동 기획이 최근 영상을 찾을 때. 없으면 채널 페이지를 읽는다 |
| `DAILY_PLAN_LIMIT` | 사용자별 하루 쇼츠 기획 횟수, 기본 20 |

## 12. 배포 절차

### 최초 1회

**사용자가 직접 할 일 (sudo, Google Cloud 콘솔)**

1. nginx 설정 넣기. SELinux 라벨 때문에 `mv`가 아니라 `cp`를 씁니다.
   ```bash
   sudo cp ~/worship-tools/deploy/bbc.ppsoft.kr.conf /etc/nginx/conf.d/bbc.ppsoft.kr.conf
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d bbc.ppsoft.kr
   ```
2. Google Cloud 콘솔
   - OAuth 클라이언트 → 승인된 JavaScript 원본에 `https://bbc.ppsoft.kr` 추가
   - OAuth 동의 화면 → 테스트 사용자에 팀원 이메일 추가
   - Gemini API 키 → 웹사이트 제한을 없애고 **API 제한을 Generative Language API로만** 설정합니다. 서버에서 호출하면 리퍼러가 없어서 웹사이트 제한이 동작하지 않습니다. 필요하면 IP 제한(`14.50.236.172`)을 추가합니다.
   - (권장) v2용 Gemini 키를 새로 만들고, 브라우저에 노출됐던 v1 키는 기록을 옮긴 뒤 삭제합니다.

**Claude가 할 일 (tstech 권한, sudo 불필요)**

3. `git clone`으로 `~/worship-tools` 받기, `.env` 작성(키 값은 사용자가 직접 입력)
4. `docker compose -f deploy/compose.yaml up -d --build`
5. `curl http://127.0.0.1:8830/api/config`로 확인하고, 백업 cron 등록(`crontab -e`, 매일 새벽, 14일치 보관)

### 이후 배포

PC에서 `ssh tstech@192.168.0.170 '~/worship-tools/deploy/deploy.sh'`를 실행하면 `git pull` → 이미지 다시 빌드 → 컨테이너 교체 순서로 진행됩니다. DB는 볼륨에 있어서 그대로 남습니다.

## 13. 작업 순서

1. **서버 골격**: FastAPI, DB 스키마, 로그인·세션·역할, `/api/me`. pytest로 권한 검사
2. **쇼츠**: 작업 실행기, Gemini 호출, 쇼츠 기록 API, 사용량 제한
3. **세트리스트 기록, 설정, 팀원 API**
4. **화면(`web/index.html`)**: v1을 복사해서 로그인, 역할별 탭, API 저장소, 작업 폴링 적용. Playwright 테스트
5. ~~v1 내보내기 버튼 + v2 가져오기~~ (2026-10-20 취소, 9장 참고)
6. **배포**: Dockerfile, compose, nginx 설정, deploy/backup 스크립트 → 170번 서버에 배포
7. **실제 검증**: 사용자의 SKILL.md와 실제 라이브 영상으로 쇼츠 카드 분리와 PDF 레이아웃 확인, 팀원 계정 로그인 확인
8. **정리**: 기록 이전이 끝나면 GitHub Pages를 v2 안내 페이지로 바꾸거나 끕니다

## 14. 남은 확인 사항

- **SKILL.md**: 아직 받지 못했습니다. v2에서는 관리자 설정 화면에서 붙여넣어 DB에 저장합니다(공개 저장소에 넣지 않음).
- **OAuth 테스트 상태에서 팀원 로그인**: 기본 범위만 쓰는 로그인도 테스트 사용자 제한을 받는지 구현할 때 확인합니다. 제한을 받으면 팀원을 테스트 사용자로 추가하면 됩니다(소수 인원).
- **팀원 명단**: 배포 후 관리자 설정 화면에서 추가합니다.
