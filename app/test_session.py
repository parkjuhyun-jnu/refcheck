# -*- coding: utf-8 -*-
"""유휴 1시간 자동 해제 테스트 (2026.09.11-02).

관리자 세션 활동 시각 추적, 서명된 접근 쿠키(발급·활동 시각), 미들웨어의 쿠키 갱신,
/api/ping. 실행: python app/test_session.py  (서버·네트워크·API 키 불필요)
"""
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949) 대비

sys.path.insert(0, str(Path(__file__).parent))

import main  # noqa: E402

_PASS = 0


def ok(cond, label):
    global _PASS
    assert cond, f"실패: {label}"
    _PASS += 1
    print(f"  ✓ {label}")


def req(**cookies):
    return SimpleNamespace(cookies=cookies, state=SimpleNamespace())


# ---------------------------------------------------------------- 1) 관리자 세션
print("[1] 관리자 세션 — 요청이 없으면 1시간, 있으면 최대 12시간")
now = time.time()
main.ADMIN_SESSIONS.clear()
main.ADMIN_SESSIONS["fresh"] = {"last": now - 100, "exp": now + 3600}
main.ADMIN_SESSIONS["idle"] = {"last": now - main.IDLE_TTL - 1, "exp": now + 3600}
main.ADMIN_SESSIONS["old"] = {"last": now - 10, "exp": now - 1}
ok(main.is_admin(req(admin_token="fresh")), "활동한 지 100초 — 유지")
ok(main.ADMIN_SESSIONS["fresh"]["last"] >= now, "요청이 활동 시각을 되돌림")
ok(not main.is_admin(req(admin_token="idle")) and "idle" not in main.ADMIN_SESSIONS,
   "1시간 넘게 요청 없음 — 해제되고 세션 삭제")
ok(not main.is_admin(req(admin_token="old")), "12시간 상한 경과 — 해제")
ok(not main.is_admin(req()), "쿠키 없음")

# ---------------------------------------------------------------- 2) 접근 쿠키
print("[2] 접근 쿠키 — 서명·발급 시각·활동 시각")
main._org_access_codes = lambda: {"테스트학회": "code-1234"}
main._access_code = lambda: ""
h = main._access_hash("code-1234")
cookie = main._make_access_cookie(h)
parsed = main._parse_access_cookie(cookie)
ok(parsed and parsed[0] == h and abs(parsed[2] - int(time.time())) <= 1, "새 쿠키는 해시·발급·활동 시각을 담는다")
ok(main._access_info(req(access_token=cookie)) == ("테스트학회", "user"), "유효한 쿠키 → 학회·역할")
ok(main._parse_access_cookie(h) is None, "옛 형식(해시만)은 무효 — 코드 다시 입력")
tampered = cookie[:-1] + ("0" if cookie[-1] != "0" else "1")
ok(main._parse_access_cookie(tampered) is None, "서명이 다르면 무효")


def forged(last_delta, issued_delta=0):
    """활동·발급 시각을 조작한 쿠키(서버 비밀로 정상 서명) — 만료 판정 검사용."""
    n = int(time.time())
    payload = f"{h}.{n - issued_delta}.{n - last_delta}"
    return f"{payload}.{main._access_sign(payload)}"


ok(main._access_info(req(access_token=forged(main.IDLE_TTL + 1))) == ("", ""),
   "활동 시각이 1시간 넘게 지났으면 해제")
ok(main._access_info(req(access_token=forged(10, main.ACCESS_TTL["user"] + 1))) == ("", ""),
   "발급 후 역할별 상한(이용자 4일) 경과면 해제")
r = req(access_token=forged(30))
main._access_info(r)
ok(not hasattr(r.state, "access_refresh"), "활동 30초 전 — 쿠키를 다시 쓰지 않음(Set-Cookie 절약)")
r = req(access_token=forged(120))
main._access_info(r)
fresh = getattr(r.state, "access_refresh", None)
ok(fresh and main._parse_access_cookie(fresh)[2] >= int(time.time()) - 1,
   "활동 2분 전 — 활동 시각을 지금으로 되돌린 쿠키를 응답에 싣도록 표시")
ok(main.has_access(req(access_token=cookie)) and not main.has_access(req(access_token=forged(main.IDLE_TTL + 1))),
   "has_access도 같은 판정")

# ---------------------------------------------------------------- 3) 실제 요청 경로(TestClient)
print("[3] 요청 경로 — 입장 → 상태 → 유휴 만료 → ping")
try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None
if TestClient is None:
    print("  (fastapi.testclient 없음 — 요청 경로 검사 생략)")
else:
    with TestClient(main.app) as c:
        r = c.post("/api/access", data={"code": "code-1234", "role_code": ""})
        ok(r.status_code == 200 and r.json().get("ok") and r.json().get("org") == "테스트학회", "코드 입장")
        tok = c.cookies.get("access_token")
        ok(tok and len(tok.split(".")) == 4, "발급된 쿠키는 서명된 4부분 형식")
        ok(c.get("/api/status").json().get("access_ok") is True, "상태 조회 — 입장 유지")
        ok(c.get("/api/ping").json() == {"admin": False, "access_ok": True}, "/api/ping — 입장 상태 반환")
        def with_cookie(value):
            c.cookies.clear()
            c.cookies.set("access_token", value)

        def set_cookie_value(resp):
            m = re.search(r"access_token=([^;]*)", resp.headers.get("set-cookie", ""))
            return m.group(1) if m else None

        # 활동 시각이 2분 지난 쿠키로 요청하면 미들웨어가 새 쿠키를 응답에 싣는다
        with_cookie(forged(120))
        r = c.get("/api/status")
        new_tok = set_cookie_value(r)
        ok(r.json().get("access_ok") is True and new_tok and new_tok != forged(120)
           and main._parse_access_cookie(new_tok)[2] >= int(time.time()) - 1,
           "활동이 있으면 응답에 갱신된 쿠키(Set-Cookie)")
        with_cookie(forged(30))
        ok(set_cookie_value(c.get("/api/status")) is None, "활동 30초 전이면 다시 싣지 않음")
        # 1시간 넘게 요청이 없던 쿠키 → 해제
        with_cookie(forged(main.IDLE_TTL + 5))
        ok(c.get("/api/status").json().get("access_ok") is False, "1시간 유휴 쿠키 — 상태 조회에서 미입장")
        ok(c.get("/api/ping").json().get("access_ok") is False, "/api/ping도 미입장")
        r = c.get("/api/results")
        ok(r.status_code == 401, "코드 필요 기능은 401")
        # 로그아웃 응답에는 갱신 쿠키를 덧쓰지 않는다
        with_cookie(forged(120))
        r = c.post("/api/access/logout")
        sc = r.headers.get("set-cookie", "")
        ok(r.status_code == 200 and 'access_token=""' in sc and sc.count("access_token=") == 1,
           "로그아웃 응답이 쿠키를 지우고 갱신으로 덮지 않음")

print(f"\n전체 {_PASS}건 통과")
