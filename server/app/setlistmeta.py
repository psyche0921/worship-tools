"""단톡방 메모에서 조성·박자·BPM·곡 구조를 뽑는다.

예배팀이 세트리스트에 손으로 적어 둔 값이라 **채보 학습의 정답 힌트**가 된다
(`docs/drdr-training.md` 5장). 공유 JSON에 담아 drdr가 읽어 간다.

    (KEY - A  | TIME - 4/4 | TEMPO - 123)
    intro(16) - V - C - (8) - V - C*3 - 나로부터*4

주의: `structure`는 **우리 팀이 이렇게 부르겠다는 계획**이다. 세트리스트의 유튜브
링크는 원곡이라 그 영상의 반복 구조와는 맞지 않는다.
"""
import re

KEY = re.compile(r'KEY\s*[-:=]?\s*([A-G][#b♯♭]?m?)\b', re.I)
TIME = re.compile(r'TIME\s*[-:=]?\s*(\d{1,2})\s*/\s*(\d{1,2})', re.I)
TEMPO = re.compile(r'TEMPO\s*[-:=]?\s*(\d{2,3})\b', re.I)
MARKERS = re.compile(r'KEY|TIME|TEMPO|BPM', re.I)
# 곡 구성 줄은 섹션을 '-'로 이어 적는다. 이만큼 이어져 있으면 구성 줄로 본다.
JOINS = 2
SECTION = re.compile(r'(?:^|[\s\-(])(?:intro|outro|inter|verse|v\d?|pre-?c(?:horus)?|prec|'
                     r'chorus|c\d?|bridge|tag|ending|hook|a|b|d)(?:\*\d+)?(?=$|[\s\-)*])', re.I)

MAX_STRUCTURE = 400


def _norm_key(raw: str) -> str:
    return raw.replace('♯', '#').replace('♭', 'b').strip()


def parse(notes) -> dict:
    """곡 메모 목록 → {key, timeSignature, tempo, structure}. 못 찾은 것은 빈 값."""
    out = {'key': '', 'timeSignature': '', 'tempo': None, 'structure': ''}
    best = ''
    for raw in (notes or []):
        line = ' '.join(str(raw).split())
        if not line:
            continue
        if not out['key']:
            m = KEY.search(line)
            if m:
                out['key'] = _norm_key(m.group(1))
        if not out['timeSignature']:
            m = TIME.search(line)
            if m:
                out['timeSignature'] = f'{int(m.group(1))}/{int(m.group(2))}'
        if out['tempo'] is None:
            m = TEMPO.search(line)
            if m:
                out['tempo'] = int(m.group(1))
        # KEY/TIME/TEMPO 줄에도 '-'가 많아서 구성 줄과 헷갈린다. 그 줄은 구성에서 뺀다.
        if MARKERS.search(line):
            continue
        if line.count('-') >= JOINS and SECTION.search(line) and len(line) > len(best):
            best = line
    out['structure'] = best[:MAX_STRUCTURE]
    return out
