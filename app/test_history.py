# -*- coding: utf-8 -*-
"""이력 보존 테스트 (2026.09.11-02) — 학회별 최근 100건 보존, 초과분은 아카이브 후 삭제.

실행: python app/test_history.py  (임시 폴더에서만 동작 — 실제 이력에 손대지 않는다)
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))

import history as history_mod  # noqa: E402

_PASS = 0


def ok(cond, label):
    global _PASS
    assert cond, f"실패: {label}"
    _PASS += 1
    print(f"  ✓ {label}")


print("[1] 학회별 최근 100건 보존")
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    history_mod.HISTORY_DIR = tmp / "history"
    history_mod.ARCHIVE_PATH = tmp / "history_archive.csv"
    history_mod.HISTORY_DIR.mkdir()
    base = time.time() - 10000

    def put(hid, org, n):
        p = history_mod.HISTORY_DIR / f"{hid}.json"
        p.write_text(json.dumps({"id": hid, "time": f"2026-09-{(n % 28) + 1:02d} 10:00",
                                 "filename": f"{org}-{n}.hwp", "org": org, "total": 1,
                                 "items": [{"group": "국내문헌", "formatted": f"항목 {n}"}]},
                                ensure_ascii=False), encoding="utf-8")
        os.utime(p, (base + n, base + n))   # n이 클수록 최근

    for n in range(103):
        put(f"h_a{n:03d}", "학회A", n)
    for n in range(5):
        put(f"h_b{n:03d}", "학회B", 200 + n)
    for n in range(2):
        put(f"h_n{n:03d}", "", 300 + n)     # 학회 없음(공통 코드·익명)
    (history_mod.HISTORY_DIR / "h_broken.json").write_text("{not json", encoding="utf-8")
    os.utime(history_mod.HISTORY_DIR / "h_broken.json", (base + 1, base + 1))

    history_mod._prune_unlocked()
    left = sorted(p.stem for p in history_mod.HISTORY_DIR.glob("h_*.json"))
    a = [x for x in left if x.startswith("h_a")]
    ok(len(a) == 100 and a[0] == "h_a003", f"학회A 103건 → 최근 100건(가장 오래된 3건 삭제) (실제 {len(a)}, 첫 {a[0]})")
    ok(sum(1 for x in left if x.startswith("h_b0")) == 5, "학회B 5건은 그대로")
    ok(sum(1 for x in left if x.startswith("h_n")) == 2, "학회 없는 기록도 자기 묶음으로 보존")
    # 깨진 파일은 학회 없음 묶음(2건)에 포함돼 3건 → 100건 이하이므로 유지된다
    ok("h_broken" in left, "깨진 JSON도 100건 이하 묶음이면 지우지 않는다")
    csv_text = history_mod.ARCHIVE_PATH.read_text(encoding="utf-8-sig")
    ok(csv_text.count("학회A-0.hwp") == 1 and csv_text.count("학회A-2.hwp") == 1 and "학회A-3.hwp" not in csv_text,
       "밀려난 3건만 아카이브 CSV에 기록")

print(f"\n전체 {_PASS}건 통과")
