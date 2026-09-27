# -*- coding: utf-8 -*-
"""학회지 발행본(오픈액세스 PDF)으로 참고문헌 편집 관행을 실측한다.

    python tools/measure_issue.py liss 57 3
    python tools/measure_issue.py kbiblia 37 3 --limit 3
    python tools/measure_issue.py kosim 43 3 --out 결과.md --json 결과.json

KCI는 새 호의 참고문헌을 약 1년 뒤에야 등록하므로 새 호 실측에는 쓸 수 없다.
대신 4개 학회지 모두 오픈액세스라 accesson.kr 첫 화면에 현재 호의 논문별 PDF가
`/{slug}/assets/pdf/<id>/journal-<권>-<호>-<시작면>.pdf` 형식으로 걸린다(2026-09 실측).

측정 항목은 학회마다 갈릴 수 있는 네 가지다. 판정을 코드가 다 하지는 못하므로
어긋난 사례는 원문을 그대로 실어 사람이 눈으로 확인할 수 있게 한다.
  1. 영문 제목 부제의 첫 낱말 대소문자 (한국도서관·정보학회지는 57권 2호부터 대문자)
  2. 온라인 자료 접두어 (출처: / Available: / 없음)
  3. DOI 주소 형식 (https://doi.org/ / 맨 도메인 / DOI: 등)
  4. 면수 표기 (붙임표 / 물결표 / pp.)
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import extract          # noqa: E402  (app 디렉터리를 경로에 넣은 뒤라야 한다)
import parsing          # noqa: E402

BASE = "https://accesson.kr"
SLUG_NAMES = {
    "liss": "한국도서관·정보학회지",
    "kslis": "한국문헌정보학회지",
    "kbiblia": "한국비블리아학회지",
    "kosim": "정보관리학회지",
    "jksarm": "한국기록관리학회지",
}


# ---------------------------------------------------------------- 내려받기

def pdf_links(slug: str, vol: int, issue: int) -> list[str]:
    """첫 화면에서 해당 권·호의 논문 PDF 주소를 모은다(시작 면수 순)."""
    import httpx
    html = httpx.get(f"{BASE}/{slug}", timeout=60, follow_redirects=True).text
    pat = re.compile(r"/%s/assets/pdf/\d+/journal-%d-%d-(\d+)\.pdf" % (slug, vol, issue))
    found = {m.group(0): int(m.group(1)) for m in pat.finditer(html)}
    return [u for u, _ in sorted(found.items(), key=lambda kv: kv[1])]


def fetch(url: str, cache: Path) -> bytes:
    """PDF를 내려받되 같은 파일은 한 번만 받는다(되풀이 실행·재시도에 대비)."""
    import httpx
    dst = cache / url.rsplit("/", 1)[-1]
    if dst.exists() and dst.stat().st_size > 1000:
        return dst.read_bytes()
    data = httpx.get(BASE + url, timeout=120, follow_redirects=True).content
    cache.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return data


def entries_of(data: bytes, name: str) -> tuple[list[str], list[str]]:
    """PDF 한 편 → (국문 목록 항목, 영문 변환 목록 항목)."""
    text = parsing.extract_text(name, data)
    _body, section = extract.find_reference_section(text)
    if not section:
        return [], []
    main, conv, _ = extract.find_en_conversion_split(section)
    return extract.split_entries(main), extract.split_entries(conv) if conv else []


# ---------------------------------------------------------------- 측정

_URL = re.compile(r"https?://\S+|\bdoi\.org/\S+", re.I)
_YEAR_HEAD = re.compile(r"\(\s*(?:n\.d\.|발행년\s*불명|(?:18|19|20)\d{2}[a-z]?)\s*\)\s*\.?\s*")
# 제목이 끝나는 자리 — 마침표 뒤에 공백이 오되 약어(U.S. / et al. / No.)는 건너뛴다
_TITLE_END = re.compile(r"(?<![A-Z])(?<!\bNo)(?<!\bpp)\.\s")


def title_of(entry: str) -> str:
    """항목에서 제목만 떼어낸다 — '저자 (연도). 제목. 나머지'.

    부제 대소문자는 제목 안의 쌍점만 봐야 한다. 단행본의 '발행지: 출판사'는
    제목 밖이라 이렇게 잘라내야 측정에 섞이지 않는다.
    """
    m = _YEAR_HEAD.search(entry)
    rest = entry[m.end():] if m else entry
    end = _TITLE_END.search(rest)
    return (rest[:end.start()] if end else rest).strip()


#  'Available: https://…', 'In: …' 처럼 쌍점을 쓰지만 부제가 아닌 이름표
_SUB_LABEL = re.compile(r"(?:Available|Retrieved\s+from|출처|URL|DOI|In|Eds?|Vol|No)\s*$", re.I)


def subtitle_case(entries: list[str]) -> list[dict]:
    """영문 제목 가운데 부제(쌍점 뒤)가 있는 것만 골라 첫 낱말의 대소문자를 본다."""
    out = []
    for e in entries:
        title = _URL.sub("", title_of(e))
        if re.search(r"[가-힣]", title):
            continue                      # 국문 제목은 대상이 아니다
        m = next((x for x in re.finditer(r":\s+([A-Za-z])(\w*)", title)
                  if not _SUB_LABEL.search(title[:x.start()])), None)
        if not m:
            continue
        out.append({"upper": m.group(1).isupper(),
                    "word": m.group(1) + m.group(2),
                    "title": title[:120], "entry": e[:200]})
    return out


def online_prefix(entries: list[str]) -> tuple[Counter, list[str]]:
    """URL을 실은 항목이 어떤 접두어를 쓰는지."""
    c, bare = Counter(), []
    for e in entries:
        if not _URL.search(e) or re.search(r"doi\.org/10\.", e):
            continue                      # DOI만 있는 항목은 온라인 자료 접두어 대상이 아니다
        if re.search(r"출처\s*:", e):
            c["출처:"] += 1
        elif re.search(r"Available\s*:", e, re.I):
            c["Available:"] += 1
        elif re.search(r"Retrieved\s+from", e, re.I):
            c["Retrieved from"] += 1
        else:
            c["접두어 없음"] += 1
            bare.append(e[:200])
    return c, bare


def doi_style(entries: list[str]) -> Counter:
    c = Counter()
    for e in entries:
        if re.search(r"https://doi\.org/10\.", e):
            c["https://doi.org/"] += 1
        elif re.search(r"http://(?:dx\.)?doi\.org/10\.", e):
            c["http://doi.org/"] += 1
        elif re.search(r"(?<!/)\bdoi\.org/10\.", e):
            c["doi.org/ (맨 도메인)"] += 1
        elif re.search(r"\bDOI\s*:", e, re.I):
            c["DOI:"] += 1
    return c


def page_style(entries: list[str]) -> Counter:
    c = Counter()
    for e in entries:
        body = _URL.sub("", e)
        if re.search(r"\d+\s*~\s*\d+", body):
            c["물결표(~)"] += 1
        elif re.search(r"\bpp?\.\s*\d+", body):
            c["pp. 붙임"] += 1
        elif re.search(r"\d+\s*[-–—]\s*\d+\s*\.?\s*$", body.strip()):
            c["붙임표(-)"] += 1
    return c


# ---------------------------------------------------------------- 실행

def measure(slug: str, vol: int, issue: int, limit: int, cache: Path) -> dict:
    urls = pdf_links(slug, vol, issue)
    if not urls:
        return {"slug": slug, "vol": vol, "issue": issue, "articles": 0, "error":
                f"{SLUG_NAMES.get(slug, slug)} {vol}권 {issue}호 PDF가 첫 화면에 없습니다(미발행이거나 주소 형식이 바뀐 것)."}
    if limit:
        urls = urls[:limit]
    res = {"slug": slug, "name": SLUG_NAMES.get(slug, slug), "vol": vol, "issue": issue,
           "articles": len(urls), "per_article": [], "subtitle": [], "prefix": Counter(),
           "prefix_bare": [], "doi": Counter(), "pages": Counter(), "entries": 0,
           "with_year": 0, "conv_entries": 0}
    for url in urls:
        name = url.rsplit("/", 1)[-1]
        try:
            main, conv = entries_of(fetch(url, cache), name)
        except Exception as ex:                      # 한 편이 깨져도 나머지는 재야 한다
            res["per_article"].append({"file": name, "error": f"{type(ex).__name__}: {ex}"})
            continue
        every = main + conv
        years = sum(1 for e in every if re.search(r"(?:18|19|20)\d{2}", e))
        res["entries"] += len(every)
        res["with_year"] += years
        res["conv_entries"] += len(conv)
        res["per_article"].append({"file": name, "entries": len(every),
                                   "conv": len(conv), "with_year": years})
        res["subtitle"] += subtitle_case(every)
        pc, bare = online_prefix(every)
        res["prefix"] += pc
        res["prefix_bare"] += bare[:3]
        res["doi"] += doi_style(every)
        res["pages"] += page_style(every)
    return res


def _counter_line(c: Counter) -> str:
    return " · ".join(f"{k} {v}건" for k, v in c.most_common()) if c else "해당 없음"


def to_markdown(results: list[dict]) -> str:
    L = [f"# 발행본 실측 결과", ""]
    for r in results:
        head = f"## {r.get('name', r['slug'])} {r['vol']}권 {r['issue']}호"
        if r.get("error"):
            L += [head, "", f"> {r['error']}", ""]
            continue
        subs = r["subtitle"]
        up = sum(1 for s in subs if s["upper"])
        rate = f"{up}/{len(subs)}" + (f" ({up * 100 // len(subs)}%)" if subs else "")
        ok = r["with_year"] * 100 // r["entries"] if r["entries"] else 0
        L += [head, "",
              f"- 논문 {r['articles']}편 · 참고문헌 {r['entries']}건"
              f"(영문 변환 목록 {r['conv_entries']}건) · 연도 포함 {ok}%",
              f"- **부제 첫 낱말 대문자: {rate}**",
              f"- 온라인 접두어: {_counter_line(r['prefix'])}",
              f"- DOI 형식: {_counter_line(r['doi'])}",
              f"- 면수 표기: {_counter_line(r['pages'])}", ""]
        minority = [s for s in subs if s["upper"] == (up * 2 < len(subs))]
        if minority:
            L += ["<details><summary>소수 쪽 사례(사람이 확인할 것)</summary>", ""]
            L += [f"- `{s['word']}` — {s['title']}" for s in minority[:15]]
            L += ["", "</details>", ""]
        if r["prefix_bare"]:
            L += ["<details><summary>접두어 없는 URL 항목</summary>", ""]
            L += [f"- {t}" for t in r["prefix_bare"][:10]] + ["", "</details>", ""]
        bad = [a for a in r["per_article"] if a.get("error") or a.get("entries", 0) < 10]
        if bad:
            L += ["> 추출이 부실한 논문(확인 필요): "
                  + ", ".join(f"{a['file']}({a.get('error') or str(a.get('entries')) + '건'})"
                              for a in bad), ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="학회지 발행본으로 참고문헌 편집 관행 실측")
    ap.add_argument("targets", nargs="+",
                    help="slug 권 호 를 세 개씩 나열 (예: liss 57 3 kbiblia 37 3)")
    ap.add_argument("--limit", type=int, default=0, help="학회지당 논문 수 제한(0=전부)")
    ap.add_argument("--cache", default=str(ROOT / "실측자료" / "pdf"), help="PDF 보관 폴더")
    ap.add_argument("--out", help="마크다운 보고서 저장 경로")
    ap.add_argument("--json", dest="js", help="원자료 JSON 저장 경로")
    a = ap.parse_args()
    if len(a.targets) % 3:
        ap.error("slug 권 호 를 세 개씩 짝지어 주세요.")

    results = []
    for i in range(0, len(a.targets), 3):
        slug, vol, issue = a.targets[i], int(a.targets[i + 1]), int(a.targets[i + 2])
        print(f"[{slug} {vol}-{issue}] 내려받는 중…", file=sys.stderr)
        results.append(measure(slug, vol, issue, a.limit, Path(a.cache)))

    md = to_markdown(results)
    print(md)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
    if a.js:
        Path(a.js).write_text(json.dumps(results, ensure_ascii=False, indent=1,
                                         default=dict), encoding="utf-8")
    return 0 if any(r.get("articles") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
