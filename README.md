# 예배팀 주간 도구

브라우저에서 동작하는 개인용 도구 페이지입니다.

- **세트리스트**: 카톡으로 받은 세트리스트 메시지를 붙여넣으면 유튜브 링크를 찾아 '일부 공개' 재생목록을 만들고 공유 링크를 복사합니다.
- **쇼츠 기획**: 주일 라이브 영상의 설교 구간을 `SKILL.md` 지침에 따라 Gemini API로 분석해 쇼츠 기획안을 만듭니다.

API 키와 OAuth 클라이언트 ID는 코드에 포함되지 않고 각 브라우저에만 저장됩니다.

## 작업 기록 · PDF

- 두 탭 모두 작업 기록이 이 브라우저에 저장되고, 다시 열거나 삭제할 수 있습니다.
- 쇼츠 기획 결과는 타임스탬프 링크가 살아있는 PDF로 공유하거나 저장할 수 있습니다.

## 포함된 외부 파일

- `vendor/jspdf.umd.min.js`: [jsPDF](https://github.com/parallax/jsPDF) 2.5.2 (MIT)
- `fonts/NanumGothic-*.ttf`: 나눔고딕 (SIL Open Font License, `fonts/OFL.txt`)
