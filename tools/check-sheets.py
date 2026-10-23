#!/usr/bin/env python3
"""악보 PDF가 '악보 프로그램에서 만든 것'인지 '스캔·사진'인지 세어 본다.

왜 필요한가: drdr 채보 학습 자료를 만드는 1단계 비용이 여기서 갈린다
(`docs/drdr-training.md` 1·6장). 프로그램 PDF면 글자 레이어에서 코드와 가사를
거의 그대로 뽑을 수 있어 OMR이 필요 없다. 스캔이면 Audiveris부터 들어가야 한다.

    pip install pymupdf
    python tools/check-sheets.py <악보가 있는 폴더> [--show 5]

폴더 안의 PDF를 모두(하위 폴더까지) 보고 종류별 개수와, 프로그램 PDF에서 찾은
코드 심볼·가사 줄의 예를 보여 준다. 파일을 고치지 않는다(읽기만).
"""
import argparse
import re
import sys
from collections import Counter
from pathlib import Path

# 코드 심볼: A, Bm7, F#m, Gsus4, Ab/C, Cmaj7, D-7(=Dm7), Am7b5, C(9), N.C.
CHORD = re.compile(r'^(?:N\.?C\.?|[A-G][#b]?'
                   r'(?:maj|min|m|M|dim|aug|sus|add|alt|[-+Δ°ø])*'
                   r'(?:\d{1,2}|[#b]\d{1,2}|sus\d|add\d|dim|aug|[-+])*'
                   r'(?:\([^)]*\))?'
                   r'(?:/[A-G][#b]?)?)$')
# 악보 글꼴(음표 글리프). 이 글꼴이 보이면 음표까지 좌표로 뽑아 볼 수 있다.
MUSIC_FONTS = ('maestro', 'opus', 'bravura', 'emmentaler', 'sonata', 'petaluma', 'leland')
# 제목 아래 저작권 줄. 가사가 아니라서 뺀다.
CREDITS = re.compile(r'작사|작곡|편곡|번역|c\.?c\.?m|©|copyright|all rights', re.I)
HANGUL = re.compile(r'[가-힣]')

# 가리는 기준은 '글자를 읽을 수 있나'다. 1쪽에 이만큼의 글자가 있으면 글자 레이어가 있다고 본다.
MIN_CHARS = 40
COVER = 0.4       # 쪽의 이 비율을 넘는 이미지가 있으면 스캔한 쪽으로 본다
ROW_TOL = 3       # 이 안(pt)에 있으면 같은 줄로 본다


def page_text_items(page):
    """(글자, 글꼴, x, y) 목록. PyMuPDF의 dict 구조에서 span 단위로 모은다."""
    out = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                text = (span.get('text') or '').strip()
                if text:
                    out.append((text, (span.get('font') or '').lower(),
                                span['bbox'][0], span['bbox'][1]))
    return out


def text_rows(items):
    """같은 y에 있는 조각을 x 순서로 한 줄로 묶는다.

    악보 프로그램은 **가사를 음절마다 따로** 적는다(한 음절이 조각 하나). 묶지 않으면
    가사가 한 글자씩 흩어져서 세어지지 않는다. 음표 글리프는 글자가 아니라서 뺀다.
    """
    rows = {}
    for text, font, x, y in items:
        if any(f in font for f in MUSIC_FONTS):
            continue
        rows.setdefault(round(y / ROW_TOL), []).append((x, text))
    out = []
    for key in sorted(rows):
        parts = sorted(rows[key])
        out.append({'tokens': [t for _x, t in parts], 'text': ''.join(t for _x, t in parts)})
    return out


def covered_by_image(page):
    """쪽을 덮는 큰 이미지가 있는가(스캔·사진의 표시)."""
    area = abs(page.rect.width * page.rect.height) or 1
    for img in page.get_images(full=True):
        try:
            for rect in page.get_image_rects(img[0]):
                if abs(rect.width * rect.height) > area * COVER:
                    return True
        except Exception:
            continue
    return False


def page_kind(page, chars):
    """글자를 읽을 수 있으면 program. 아니면 덮는 이미지가 있으면 scanned."""
    if chars >= MIN_CHARS:
        return 'program'
    return 'scanned' if covered_by_image(page) else 'unclear'


def inspect(path, fitz):
    """한 파일을 본다. {'kind', 'pages', 'chords', 'lyrics', 'fonts'}"""
    try:
        doc = fitz.open(path)
    except Exception as e:
        return {'kind': 'error', 'error': str(e)}
    try:
        chords, lyrics, fonts, chars = [], [], set(), 0
        page_kinds = Counter()
        for page in doc:
            items = page_text_items(page)
            page_chars = sum(len(t) for t, *_ in items)
            chars += page_chars
            kind = page_kind(page, page_chars)
            page_kinds[kind] += 1
            fonts |= {f for _t, f, _x, _y in items if any(m in f for m in MUSIC_FONTS)}
            if kind != 'program':
                continue
            for row in text_rows(items):
                # 코드는 조각마다, 가사는 줄마다 센다(코드 줄에는 한글이 없고, 가사 줄의
                # 조각은 음절이라 코드로 걸리지 않는다).
                chords += [t for t in row['tokens'] if len(t) <= 10 and CHORD.match(t)]
                if HANGUL.search(row['text']) and not CREDITS.search(row['text']):
                    lyrics.append(row['text'])   # 제목·저작권 줄은 뺀다
        pages = doc.page_count
        # 쪽마다 가린 뒤 가장 많은 쪽을 그 파일의 종류로 본다(program이 반이면 program).
        kind = 'error' if not pages else (
            'program' if page_kinds['program'] * 2 >= pages
            else max(page_kinds, key=lambda k: (page_kinds[k], k == 'scanned')))
        return {'kind': kind, 'pages': pages, 'chords': chords, 'lyrics': lyrics,
                'fonts': sorted(fonts), 'chars': chars}
    finally:
        doc.close()


KIND_LABEL = {'program': '악보 프로그램에서 만든 PDF (OMR 불필요)',
              'scanned': '스캔·사진 (OMR 필요)',
              'unclear': '가리기 어려움 (글자가 적고 이미지도 작음)',
              'error': '열지 못함'}


def main():
    ap = argparse.ArgumentParser(description='악보 PDF 종류와 뽑히는 정보를 확인한다.')
    ap.add_argument('folder', help='악보 PDF가 있는 폴더 (하위 폴더까지 본다)')
    ap.add_argument('--show', type=int, default=3, help='자세히 보여 줄 파일 수 (기본 3)')
    args = ap.parse_args()

    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # PyMuPDF 1.x
        except ImportError:
            sys.exit('PyMuPDF가 없어요.  pip install pymupdf')

    root = Path(args.folder)
    if not root.is_dir():
        sys.exit(f'폴더가 아니에요: {root}')
    files = sorted(p for p in root.rglob('*') if p.suffix.lower() == '.pdf')
    if not files:
        sys.exit(f'PDF를 찾지 못했어요: {root}')

    kinds, shown = Counter(), 0
    chord_total = lyric_total = 0
    for path in files:
        r = inspect(path, fitz)
        kinds[r['kind']] += 1
        if r['kind'] == 'program':
            chord_total += len(r['chords'])
            lyric_total += len(r['lyrics'])
        if shown < args.show and r['kind'] in ('program', 'scanned', 'unclear'):
            shown += 1
            print(f"\n── {path.relative_to(root)}  [{KIND_LABEL[r['kind']]}]")
            print(f"   {r['pages']}쪽 · 글자 {r['chars']}자"
                  + (f" · 악보 글꼴 {', '.join(r['fonts'])}" if r['fonts'] else ''))
            if r['chords']:
                uniq = list(dict.fromkeys(r['chords']))
                print(f"   코드 {len(r['chords'])}개: {' '.join(uniq[:14])}")
            if r['lyrics']:
                print(f"   가사 예: {r['lyrics'][0][:60]}")

    print(f'\n== PDF {len(files)}개 ==')
    for kind in ('program', 'scanned', 'unclear', 'error'):
        if kinds[kind]:
            pct = kinds[kind] * 100 // len(files)
            print(f'  {kinds[kind]:4d}개 ({pct:2d}%)  {KIND_LABEL[kind]}')
    if kinds['program']:
        print(f"\n프로그램 PDF {kinds['program']}개에서 코드 {chord_total}개, 가사 줄 {lyric_total}개를 찾았어요.")
        print('이 비율이 높으면 docs/drdr-training.md의 1단계(코드·가사 뽑기)를 OMR 없이 바로 할 수 있어요.')
    if kinds['scanned']:
        print(f"스캔 {kinds['scanned']}개는 Audiveris(2단계)가 필요해요.")


if __name__ == '__main__':
    main()
