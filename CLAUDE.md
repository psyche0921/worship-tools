# worship-tools

교회 예배팀이 매주 반복하는 작업 두 가지를 돕는 개인용 웹 도구입니다.

1. **세트리스트 → 유튜브 재생목록**: 카톡 단톡방으로 받은 세트리스트 메시지를 붙여넣으면, 유튜브 링크를 찾아 '일부 공개' 재생목록을 만들고 공유 링크를 복사할 수 있게 합니다.
2. **설교 쇼츠 기획**: 주일 예배 라이브 영상의 설교 구간을 Gemini API로 분석합니다. 사용자의 `SKILL.md`(예전 Gemini Gem 지침)에 따라 쇼츠 기획안을 만들고, PDF로 공유할 수 있게 합니다.

사용자는 한국어로 소통합니다. UI 문구, 커밋 메시지, 설명은 모두 한국어로 작성합니다.

**지금 진행할 작업은 `docs/handoff.md`에 있습니다. 먼저 읽어주세요.** 현재 구조와 결정 사항, 다음 단계(자체 서버 배포와 DB 이전), 정해지지 않은 질문을 정리해 두었습니다.

**v2 설계는 `docs/v2-design.md`에 확정되어 있습니다.** 배포 대상은 170번 서버(`tstech@192.168.0.170`), 주소는 `https://bbc.ppsoft.kr`입니다. 팀원은 쇼츠 기획만 쓰고, 세트리스트는 관리자만 씁니다.

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

v2 서버(`server/`, FastAPI + SQLite) 테스트:

```bash
python3 -m venv .venv && .venv/Scripts/python -m pip install -r server/requirements-dev.txt
.venv/Scripts/python -m pytest -q   # Google ID 토큰 검증은 tests/conftest.py의 가짜로 대체
```

`tests/fixtures/`에는 실제 단톡방 메시지 형식의 샘플과, `#t=` 표기가 섞인 쇼츠 응답 샘플이 있습니다.
