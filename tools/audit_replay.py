# -*- coding: utf-8 -*-
"""오판 점검 결과(audit/kci2025/results.jsonl)를 네트워크 없이 다시 잰다 — 수정 효과 확인용.

조회(verify)는 다시 하지 않는다. 기록된 항목(entry)과 조회 결과(v.meta)를 그대로 넣어
교정 제안(_build_suggestions)·면수 비고(_page_check_note)·형식 변환(format_entry)·
형식 경고(validate_entry, lost_elements)만 지금 코드로 다시 계산한다. 그 층을 고쳤을 때
오판 후보가 몇 건 줄었는지, 새로 생긴 것은 없는지 바로 볼 수 있다. API 키가 없는
클라우드 세션에서도 돈다.

조회 층(verify.py의 짝짓기·판정)을 고친 효과는 이것으로 잴 수 없다 — 그 경우는 기록된
meta로 단위 시험을 만들어 확인한다(작업지시서 참조).

    python tools/audit_replay.py                     # 신호별 전후 비교
    python tools/audit_replay.py --show suggest:year # 그 신호가 사라지거나 새로 생긴 항목
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_published as ap  # noqa: E402  (Claude API 차단·app 경로 설정을 함께 가져온다)

formatter, main, rules = ap.formatter, ap.main, ap.rules


def replay(row: dict) -> list[str]:
    e = rules.new_entry(row["entry"].get("raw", ""))
    e.update(row["entry"])
    v = row["v"]
    st = v.get("status", "")
    flags = [f for f in row["flags"] if f.startswith("status:")]   # 조회 층 판정은 그대로
    sugg, pnote = [], []
    if st == "verified":
        sugg = main._build_suggestions(e, v.get("meta"))
        pnote = main._page_check_note(e, v.get("meta"))
    formatted = formatter.format_entry(e, ap.JOURNALS[row["j"]][1])
    issues = formatter.validate_entry(e) + formatter.lost_elements(e.get("raw", ""), formatted)
    flags += ["suggest:" + s.get("field", "?") for s in sugg]
    flags += ["page_note"] if pnote else []
    flags += ["issue"] * bool(issues)
    flags += ["loss:" + x for x in ap.field_loss(e, formatted)]
    return flags


def main_cli() -> int:
    p = argparse.ArgumentParser(description="오판 점검 결과를 지금 코드로 다시 재기(조회 없음)")
    p.add_argument("--show", help="이 신호가 사라지거나 새로 생긴 항목을 보여 준다")
    a = p.parse_args()
    rows = [json.loads(x) for x in ap.RESULTS.read_text(encoding="utf-8").splitlines() if x.strip()]
    before, after = Counter(), Counter()
    gone, came = [], []
    cand_b = cand_a = 0
    for r in rows:
        old, new = set(r["flags"]), set(replay(r))
        before.update(old)
        after.update(new)
        cand_b += bool(old)
        cand_a += bool(new)
        if a.show and (a.show in old) != (a.show in new):
            (gone if a.show in old else came).append(r)
    print(f"오판 후보: {cand_b} → {cand_a} (참고문헌 {len(rows)}건)")
    print(f"{'신호':<22}{'전':>7}{'후':>7}")
    for k in sorted(set(before) | set(after), key=lambda k: -before[k]):
        mark = "" if before[k] == after[k] else ("  ↓" if after[k] < before[k] else "  ↑ 새로 생김")
        print(f"{k:<22}{before[k]:>7}{after[k]:>7}{mark}")
    for label, lst in (("사라진", gone), ("새로 생긴", came)):
        if lst:
            print(f"\n== {a.show} {label} 항목 {len(lst)}건 (앞 15건)")
            for r in lst[:15]:
                print(f"- [{r['j']} {r['art']} #{r['i']}] {r['formatted'][:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
