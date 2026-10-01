# -*- coding: utf-8 -*-
"""게재 논문 참고문헌으로 refcheck의 오판을 전수 점검한다 — 1단계: 조회(이 PC).

4개 학회지 2025년 게재 논문의 참고문헌(KCI 등록 구조화 필드, 실측자료/kci2025)을 이용자
원고처럼 refcheck에 넣고, 운영 코드의 검증(verify_entries)·교정 제안(_build_suggestions)·
면수 비고(_page_check_note)·형식 변환(format_entry)·형식 경고(validate_entry,
lost_elements)를 그대로 통과시킨다. 게재본은 편집위원회 검토를 거친 최종본이라 거의 맞으므로,
refcheck가 '틀렸다'고 하는 곳은 대부분 refcheck 쪽 오판 후보다.

KCI·RISS·국립중앙도서관 키가 .env에 있어야 하므로 이 PC(또는 운영 서버)에서만 돈다.
Claude API는 부르지 않는다 — 혹시라도 불리면 예외로 멈추게 막아 둔다(콘솔 비용 0원).
AI 구조화 단계를 건너뛰고 KCI 필드를 '완벽한 구조화'로 쓰므로, 여기서 잡히는 것은
검증·교정 제안·형식 변환 쪽 오판이다(구조화 오류는 범위 밖).

2단계(판정·수정)는 결과 파일을 받은 클라우드 세션이 맡는다 — audit/kci2025/작업지시서.md.

    python tools/audit_published.py                 # 전체(중단돼도 이어서 한다)
    python tools/audit_published.py --limit 2       # 학회지당 2편만(시험)
    python tools/audit_published.py --summary-only  # 결과 파일로 요약만 다시 만들기
"""
import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import aiengine  # noqa: E402


def _no_ai(*_a, **_k):
    raise RuntimeError("점검 하네스는 Claude API를 부르지 않습니다(콘솔 비용 0원 유지)")


aiengine._client = _no_ai   # main을 불러오기 전에 막아 둔다

import formatter            # noqa: E402
import main                 # noqa: E402  운영과 같은 _build_suggestions·_page_check_note
import rules                # noqa: E402
import verify as verify_mod  # noqa: E402

SRC = ROOT / "실측자료" / "kci2025"
OUT = ROOT / "audit" / "kci2025"
RESULTS = OUT / "results.jsonl"
SUMMARY = OUT / "요약.md"

JOURNALS = {  # slug: (학술지명, refcheck 학회명 — 학회별 관행 층 ORG_SUBTITLE_CASE의 열쇠)
    "liss": ("한국도서관·정보학회지", "한국도서관정보학회"),
    "kslis": ("한국문헌정보학회지", "한국문헌정보학회"),
    "kbiblia": ("한국비블리아학회지", "한국비블리아학회"),
    "kosim": ("정보관리학회지", "한국정보관리학회"),
}
# KCI <reference type-code> → refcheck 자료 유형
TYPE = {"01": "journal", "02": "conference", "03": "book", "04": "report",
        "05": "thesis", "06": "web", "07": "unknown"}


# ---------------------------------------------------------------- KCI 필드 → refcheck 항목

def _s(x) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip()


def _pages(p) -> str:
    p = _s(p).replace("–", "-").replace("—", "-")
    return "" if p in ("-", "0", "0-0") else p


def _doi(d) -> str:
    d = _s(d)
    d = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", d, flags=re.I)
    return d if d.startswith("10.") else ""


def _num(x) -> str:
    x = _s(x)
    return "" if x in ("0", "00", "-") else x


def _name(a: str, lang: str) -> str:
    """KCI의 'Wilkinson M'(밴쿠버식)을 refcheck가 받는 'Wilkinson, M.'으로. 나머지는 그대로."""
    if lang == "west":
        m = re.fullmatch(r"([A-Z][A-Za-z'\-]+) ([A-Z]{1,3})", a)
        if m:
            return f"{m.group(1)}, " + " ".join(c + "." for c in m.group(2))
    return a


def _degree(d, lang: str) -> str:
    d = _s(d).lower()
    master = "석사" in d or "master" in d or d == "mres"
    doctor = "박사" in d or "doctor" in d or "ph" in d
    if lang == "ko":
        return "석사학위논문" if master else "박사학위논문" if doctor else ""
    return "Master's thesis" if master else "Doctoral dissertation" if doctor else ""


def to_entry(f: dict) -> dict:
    code = f.get("type-code") or f.get("_type_code") or ""
    t = TYPE.get(code, "unknown")
    title = _s(f.get("title"))
    e = rules.new_entry("")
    e["type"] = t
    e["lang"] = rules.detect_lang(title or _s(f.get("author")))
    e["title"] = title
    e["authors"] = [_name(a, e["lang"]) for a in re.split(r"\s*;\s*", _s(f.get("author"))) if a]
    m = re.search(r"(?:18|19|20)\d{2}", _s(f.get("pubi-year")))
    e["year"] = m.group(0) if m else ""
    if t in ("journal", "conference"):
        e["container"] = _s(f.get("journal-name") or f.get("conference-name"))
        vol, iss, ser = _num(f.get("volume")), _num(f.get("isseue")), _num(f.get("serno"))
        if not vol and not iss and ser:
            vol = ser               # 권·호 없이 통권만 있는 학술지
        e["volume"], e["issue"] = vol, iss
        e["pages"] = _pages(f.get("page"))
    if t in ("book", "report", "conference"):
        e["publisher"] = _s(f.get("pubilisher"))
    if t == "thesis":
        e["degree"] = _degree(f.get("degree"), e["lang"])
        e["institution"] = _s(f.get("university"))
    if t == "web":
        e["url"] = _s(f.get("url"))
        e["container"] = _s(f.get("site-name") or f.get("pubilisher"))
    e["doi"] = _doi(f.get("doi"))
    e["raw"] = _raw(e)
    return e


def _raw(e: dict) -> str:
    """원고 원문 자리에 둘 문자열 — 문편협 꼴로 필드를 이어 붙인다(형식 변환이 무엇을 잃는지 보려고)."""
    au = ", ".join(e["authors"])
    y = e["year"] or "발행년불명"
    bits = [f"{au} ({y})." if au else f"{e['title']}. ({y})."]
    if au and e["title"]:
        bits.append(e["title"] + ".")
    t = e["type"]
    if t in ("journal", "conference") and e["container"]:
        seg = e["container"]
        if e["volume"]:
            seg += f", {e['volume']}" + (f"({e['issue']})" if e["issue"] else "")
        elif e["issue"]:
            seg += f", {e['issue']}"
        if e["pages"]:
            seg += f", {e['pages']}"
        bits.append(seg + ".")
    elif t == "thesis":
        bits.append(f"{e['degree'] or '학위논문'}, {e['institution']}.".replace(", .", "."))
    elif t in ("book", "report") and e["publisher"]:
        bits.append(e["publisher"] + ".")
    elif t == "web" and e["container"]:
        bits.append(e["container"] + ".")
    if e["doi"]:
        bits.append("https://doi.org/" + e["doi"])
    elif e["url"]:
        bits.append(("출처: " if e["lang"] == "ko" else "Available: ") + e["url"])
    return " ".join(bits)


def mark_conversions(entries: list[dict]) -> None:
    """같은 논문 목록 안의 영문 변환 항목 표시 — 국문 항목과 DOI가 같거나(학술지는)
    연도·권·호·면수가 같은 비국문 항목. 소절 표제가 없어 짝으로만 가린다."""
    def key(e):
        if e["doi"]:
            return ("doi", e["doi"].lower())
        if e["type"] == "journal" and e["pages"] and e["year"]:
            return ("vip", e["year"], e["volume"], e["issue"], e["pages"])
        return None
    groups = defaultdict(list)
    for i, e in enumerate(entries):
        k = key(e)
        if k:
            groups[k].append(i)
    for idxs in groups.values():
        if any(entries[i]["lang"] == "ko" for i in idxs):
            for i in idxs:
                if entries[i]["lang"] != "ko":
                    entries[i]["is_en_conversion"] = True


# ---------------------------------------------------------------- 판정 수집

_NORM = re.compile(r"\s+")
_LETTERS = re.compile(r"[^0-9A-Za-z가-힣]")


def field_loss(e: dict, formatted: str) -> list[str]:
    """KCI에 있던 값이 형식 변환 뒤 사라졌나 — '원문과 같은데 빨간색' 류의 근원."""
    F = _NORM.sub("", formatted).lower()
    out = []
    for k in ("year", "volume", "issue", "pages", "doi"):
        v = _NORM.sub("", e.get(k) or "").lower()
        if v and v not in F:
            out.append(k)
    if e.get("url") and not e.get("doi") and _NORM.sub("", e["url"]).lower() not in F:
        out.append("url")
    tt = _LETTERS.sub("", e.get("title") or "").lower()
    if tt and tt not in _LETTERS.sub("", formatted).lower():
        out.append("title")
    return out


def _trim(x, depth: int = 0):
    """결과 파일 크기를 줄인다 — 빈 값을 빼고 긴 문자열·목록을 자른다."""
    if isinstance(x, dict):
        return {k: _trim(v, depth + 1) for k, v in x.items()
                if v not in (None, "", [], {}) and depth < 4}
    if isinstance(x, list):
        return [_trim(v, depth + 1) for v in x[:12]]
    if isinstance(x, str):
        return x[:400]
    return x


def audit_article(slug: str, art: dict) -> list[dict]:
    fields = art.get("ref_fields") or art.get("refs_fields") or art.get("refs_raw") or []
    org = JOURNALS[slug][1]
    entries = [to_entry(f) for f in fields]
    mark_conversions(entries)
    for e in entries:
        rules.backfill_from_raw(e)
    vres = verify_mod.verify_entries(entries)
    rows = []
    for i, (f, e, v) in enumerate(zip(fields, entries, vres)):
        sugg, pnote = [], []
        if v.get("status") == "verified":       # 운영과 같은 조건(main._process_file 5단계)
            if v.get("found_doi") and not e.get("doi"):
                e["doi"] = v["found_doi"]
            sugg = main._build_suggestions(e, v.get("meta"))
            pnote = main._page_check_note(e, v.get("meta"))
        formatted = formatter.format_entry(e, org)
        issues = formatter.validate_entry(e) + formatter.lost_elements(e.get("raw", ""), formatted)
        loss = field_loss(e, formatted)
        st, det = v.get("status", ""), v.get("detail") or ""
        flags, note = [], ""
        if st == "skipped":
            # refcheck가 '틀렸다'고 한 것이 아니다 — 오판 후보에서 빼고 따로 센다
            note = "retry" if ("일시 오류" in det or "조회 실패" in det) else "not_checked"
        elif st not in ("verified", "link_ok"):
            flags.append("status:" + st)
        flags += ["suggest:" + s.get("field", "?") for s in sugg]
        flags += ["page_note"] if pnote else []
        flags += ["issue"] * bool(issues)
        flags += ["loss:" + x for x in loss]
        rows.append({
            "j": slug, "art": art.get("kci_id", ""), "art_title": (art.get("title") or "")[:120],
            "vol": art.get("volume", ""), "iss": art.get("issue", ""), "i": i,
            "kci": _trim(f), "entry": _trim({k: v2 for k, v2 in e.items() if k != "notes"}),
            "v": _trim({k: v.get(k) for k in ("status", "source", "detail", "found_doi",
                                              "journal", "retraction", "meta")}),
            "sugg": sugg, "pnote": pnote, "issues": issues, "loss": loss,
            "formatted": formatted, "flags": flags, "note": note,
        })
    return rows


# ---------------------------------------------------------------- 요약

def summarize(rows: list[dict]) -> str:
    n = len(rows)
    flagged = [r for r in rows if r["flags"]]
    exact = Counter(f for r in rows for f in set(r["flags"]))
    by_j = defaultdict(Counter)
    by_t = defaultdict(Counter)
    for r in rows:
        key = "오판 후보" if r["flags"] else "통과"
        by_j[r["j"]][key] += 1
        by_t[r["entry"].get("type", "?")][key] += 1
    det = Counter(re.sub(r"\d+%|\(\d+\)|\d{2,}", "#", (r["v"].get("detail") or ""))[:70]
                  for r in rows if any(f.startswith("status:") for f in r["flags"]))
    notes = Counter(r.get("note") for r in rows)
    L = ["# 2025년 게재 논문 참고문헌으로 본 refcheck 오판 후보", "",
         f"- 대상: 4개 학회지 2025년 게재 논문 {len({r['art'] for r in rows})}편, 참고문헌 {n}건",
         f"- **오판 후보(무엇이든 '틀렸다'고 한 항목): {len(flagged)}건 ({len(flagged) * 100 // max(n, 1)}%)**",
         f"- 검증 대상 아님(조회하지 않는 자료 — 오판 아님, 범위 문제): {notes.get('not_checked', 0)}건",
         f"- 조회 실패(외부 DB 일시 오류 — 오판 아님, 다시 조회 대상): {notes.get('retry', 0)}건", "",
         "## 신호별 건수", "", "| 신호 | 건수 |", "| --- | ---: |"]
    L += [f"| `{k}` | {v} |" for k, v in exact.most_common()]
    L += ["", "## 학회지별", "", "| 학회지 | 통과 | 오판 후보 |", "| --- | ---: | ---: |"]
    L += [f"| {JOURNALS[j][0]} | {c['통과']} | {c['오판 후보']} |" for j, c in by_j.items()]
    L += ["", "## 자료 유형별", "", "| 유형 | 통과 | 오판 후보 |", "| --- | ---: | ---: |"]
    L += [f"| {t} | {c['통과']} | {c['오판 후보']} |" for t, c in sorted(by_t.items())]
    L += ["", "## 미확인·불일치 사유(상위 25)", ""]
    L += [f"- {v}건 — {k}" for k, v in det.most_common(25)]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- 실행

def load_done() -> tuple[set, list]:
    rows, done = [], set()
    if RESULTS.exists():
        for line in RESULTS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rows.append(r)
                done.add(r["art"])
    return done, rows


def main_cli() -> int:
    ap = argparse.ArgumentParser(description="게재 논문 참고문헌으로 refcheck 오판 전수 점검(조회 단계)")
    ap.add_argument("--limit", type=int, default=0, help="학회지당 논문 수 제한(0=전부)")
    ap.add_argument("--only", nargs="*", default=list(JOURNALS), help="학회지 slug 골라 돌리기")
    ap.add_argument("--summary-only", action="store_true")
    ap.add_argument("--retry-failed", action="store_true", help="조회 실패가 있던 논문만 다시 조회")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    done, rows = load_done()
    if a.retry_failed:
        redo = {r["art"] for r in rows if r.get("note") == "retry"}
        rows = [r for r in rows if r["art"] not in redo]
        RESULTS.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                           encoding="utf-8")
        done -= redo
        print(f"조회 실패가 있던 논문 {len(redo)}편을 다시 조회합니다.", file=sys.stderr)
    if not a.summary_only:
        for slug in a.only:
            arts = json.loads((SRC / f"{slug}.json").read_text(encoding="utf-8"))["articles"]
            if a.limit:
                arts = arts[:a.limit]
            for k, art in enumerate(arts, 1):
                if art.get("kci_id") in done:
                    continue
                t0 = time.time()
                new = audit_article(slug, art)
                with RESULTS.open("a", encoding="utf-8") as fp:
                    for r in new:
                        fp.write(json.dumps(r, ensure_ascii=False) + "\n")
                rows += new
                done.add(art.get("kci_id"))
                fails = sum(r["note"] == "retry" for r in new)
                bad = sum(bool(r["flags"]) for r in new)
                print(f"[{slug} {k}/{len(arts)}] {art.get('kci_id')} {len(new)}건 · 후보 {bad} · "
                      f"조회실패 {fails} · {time.time() - t0:.0f}초", file=sys.stderr, flush=True)
                if len(new) >= 8 and fails > len(new) * 0.5:
                    print("외부 DB 조회 실패가 몰렸습니다(한도 초과 가능성). 잠시 뒤 같은 명령으로 "
                          "이어서 실행하세요.", file=sys.stderr)
                    SUMMARY.write_text(summarize(rows), encoding="utf-8")
                    return 3
    SUMMARY.write_text(summarize(rows), encoding="utf-8")
    print(SUMMARY.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
