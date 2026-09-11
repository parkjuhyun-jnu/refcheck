# -*- coding: utf-8 -*-
"""국가법령정보센터 법령 대조 테스트 (2026.09.11-03).

법제처 Open API 응답을 본떠 만든 XML로 law_search·verify_entry(법령)·교정 제안·변환 짝을
검사한다(네트워크 불필요). `--live`를 주면 실제 API도 한 번 호출한다.
실행: python app/test_law.py [--live]
"""
import sys
from pathlib import Path
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))

import verify_kr  # noqa: E402
import verify  # noqa: E402
import main  # noqa: E402
from http_util import LookupUnavailable  # noqa: E402

_PASS = 0


def ok(cond, label):
    global _PASS
    assert cond, f"실패: {label}"
    _PASS += 1
    print(f"  ✓ {label}")


LAW_XML = """<?xml version="1.0" encoding="UTF-8"?><LawSearch><target>law</target><totalCnt>2</totalCnt>
<law id="1"><법령일련번호>284075</법령일련번호><현행연혁코드>현행</현행연혁코드><법령명한글><![CDATA[독서문화진흥법]]></법령명한글>
<법령약칭명><![CDATA[]]></법령약칭명><법령ID>010360</법령ID><공포일자>20260305</공포일자><공포번호>21447</공포번호>
<제개정구분명>타법개정</제개정구분명><소관부처명>문화체육관광부</소관부처명><법령구분명>법률</법령구분명><시행일자>20260701</시행일자></law>
<law id="2"><법령일련번호>287295</법령일련번호><현행연혁코드>현행</현행연혁코드><법령명한글><![CDATA[독서문화진흥법 시행령]]></법령명한글>
<법령약칭명><![CDATA[]]></법령약칭명><법령ID>010418</법령ID><공포일자>20260623</공포일자><공포번호>36424</공포번호>
<제개정구분명>타법개정</제개정구분명><소관부처명>문화체육관광부</소관부처명><법령구분명>대통령령</법령구분명><시행일자>20260701</시행일자></law>
</LawSearch>"""
SCHOOL_XML = """<?xml version="1.0" encoding="UTF-8"?><LawSearch><target>law</target><totalCnt>1</totalCnt>
<law id="1"><현행연혁코드>현행</현행연혁코드><법령명한글><![CDATA[학교도서관진흥법]]></법령명한글><법령약칭명><![CDATA[학교도서관법]]></법령약칭명>
<법령ID>010584</법령ID><공포일자>20211207</공포일자><공포번호>18547</공포번호><제개정구분명>타법개정</제개정구분명>
<소관부처명>교육부</소관부처명><법령구분명>법률</법령구분명><시행일자>20221208</시행일자></law></LawSearch>"""
ELAW_XML = """<?xml version="1.0" encoding="UTF-8"?><LawSearch><target>elaw</target><totalCnt>2</totalCnt>
<law id="1"><현행연혁코드>연혁</현행연혁코드><법령명한글><![CDATA[<strong class="tbl_tx_type">독서</strong>문화진흥법]]></법령명한글>
<법령명영문><![CDATA[READING CULTURE PROMOTION ACT]]></법령명영문><법령ID>010360</법령ID><공포번호>19794</공포번호><법령구분명>법률</법령구분명></law>
<law id="2"><현행연혁코드>연혁</현행연혁코드><법령명한글><![CDATA[독서문화진흥법 시행령]]></법령명한글>
<법령명영문><![CDATA[ENFORCEMENT DECREE OF THE READING CULTURE PROMOTION ACT]]></법령명영문><법령ID>010418</법령ID><법령구분명>대통령령</법령구분명></law>
</LawSearch>"""
ELAW_SCHOOL_XML = """<?xml version="1.0" encoding="UTF-8"?><LawSearch><target>elaw</target><totalCnt>1</totalCnt>
<law id="1"><법령명한글><![CDATA[학교도서관진흥법]]></법령명한글><법령명영문><![CDATA[SCHOOL LIBRARY PROMOTION ACT]]></법령명영문></law></LawSearch>"""
EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?><LawSearch><target>law</target><totalCnt>0</totalCnt></LawSearch>"""
OC_ERROR_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Response><result>사용자 정보 검증에 실패하였습니다.</result><msg>OPEN API 호출 시 사용자 검증을 위하여 정확한 서버장비의 IP주소 및 도메인주소를 등록해 주세요.</msg></Response>"""


def fake_get(client, url, params):
    q, target = params.get("query", ""), params.get("target")
    if params.get("OC") == "bad":
        return SimpleNamespace(text=OC_ERROR_XML)
    if target == "law":
        if "독서문화진흥법" in q:
            return SimpleNamespace(text=LAW_XML)
        if q in ("학교도서관진흥법", "학교도서관법"):
            return SimpleNamespace(text=SCHOOL_XML)
        return SimpleNamespace(text=EMPTY_XML)
    if target == "elaw":
        if "독서문화진흥법" in q:
            return SimpleNamespace(text=ELAW_XML)
        if "학교도서관" in q:
            return SimpleNamespace(text=ELAW_SCHOOL_XML)
        return SimpleNamespace(text=EMPTY_XML)
    return None


verify_kr._get = fake_get
client = object()

print("[1] 보조 함수")
ok(verify_kr.law_title_case("ENFORCEMENT DECREE OF THE READING CULTURE PROMOTION ACT")
   == "Enforcement Decree of the Reading Culture Promotion Act", "영문 법령명 Title Case(관사·전치사 소문자)")
ok(verify_kr._law_date("20260305") == "2026. 3. 5.", "공포일자 → 국문 날짜 표기")
ok(verify._law_no_digits("법률 제21447호") == "21447" and verify._law_no_digits("Act No. 21447") == "21447"
   and verify._law_no_digits("") == "", "공포번호 숫자 추출(국문·영문)")

print("[2] law_search")
law = verify_kr.law_search(client, "독서문화진흥법")
ok(law and law["name"] == "독서문화진흥법" and law["no_label"] == "법률 제21447호"
   and law["name_en"] == "Reading Culture Promotion Act", "본법 — 현행 공포번호·영문 법령명")
dec = verify_kr.law_search(client, "독서문화진흥법 시행령")
ok(dec and dec["no_label"] == "대통령령 제36424호" and dec["name_en"].startswith("Enforcement Decree"),
   "시행령은 이름이 정확히 같아야 잡힘(본법과 혼동 없음)")
ok(verify_kr.law_search(client, "「독서문화진흥법」") and verify_kr.law_search(client, "독서문화 진흥법"),
   "낫표·띄어쓰기 차이 흡수")
abbr = verify_kr.law_search(client, "학교도서관법")
ok(abbr and abbr["name"] == "학교도서관진흥법" and abbr["by_abbr"] and abbr["name_en"] == "School Library Promotion Act",
   "약칭('학교도서관법') → 정식 명칭·영문명")
ok(verify_kr.law_search(client, "없는법령") is None, "없는 법령은 None")
_oc = verify_kr.law_oc
verify_kr.law_oc = lambda: "bad"
try:
    verify_kr.law_search(client, "독서문화진흥법")
    ok(False, "OC 오류")
except LookupUnavailable as ex:
    ok("사용자 정보 검증" in str(ex), "OC 오류(HTTP 200 + Response)는 '확인 못 함'(LookupUnavailable)")
verify_kr.law_oc = _oc

print("[3] verify_entry 법령 분기 · 교정 제안 · 변환 짝")
e = {"type": "law", "lang": "ko", "title": "독서문화진흥법", "report_no": "법률 제21447호",
     "raw": "독서문화진흥법. 법률 제21447호."}
v = verify.verify_entry(client, e)
ok(v["status"] == "verified" and v["source"] == "국가법령정보센터" and "현행 법률 제21447호" in v["detail"],
   "현행 공포번호 일치 — 실존 확인")
ok(v["meta"]["title_en"] == "Reading Culture Promotion Act" and v["meta"]["law_url"].endswith("독서문화진흥법"),
   "영문 법령명이 meta.title_en(영문 변환 목록 근거)에, 법령 페이지 링크")
ok(main._build_suggestions(e, v["meta"]) == [], "일치하면 제안 없음")
e2 = {"type": "law", "lang": "ko", "title": "학교도서관진흥법", "report_no": "법률 제15368호",
      "raw": "학교도서관진흥법. 법률 제15368호."}
v2 = verify.verify_entry(client, e2)
ok(v2["status"] == "verified" and "현행 공포번호와 다름" in v2["detail"], "옛 공포번호 — 실존은 확인, 차이를 안내")
ok(main._build_suggestions(e2, v2["meta"]) == [{"field": "report_no", "label": "공포번호", "current": "법률 제15368호",
                                               "suggested": "법률 제18547호", "source": "국가법령정보센터"}],
   "현행 공포번호로 갱신 제안")
e3 = {"type": "law", "lang": "ko", "title": "학교도서관법", "report_no": "법률 제18547호", "raw": "학교도서관법. 법률 제18547호."}
v3 = verify.verify_entry(client, e3)
ok("약칭" in v3["detail"] and main._build_suggestions(e3, v3["meta"])[0]["suggested"] == "학교도서관진흥법",
   "약칭은 정식 명칭 제안")
e4 = {"type": "law", "lang": "ko", "title": "없는법령", "report_no": "", "raw": "없는법령."}
v4 = verify.verify_entry(client, e4)
ok(v4["status"] == "not_found" and "정식 명칭" in v4["detail"], "없는 법령명은 미확인 + 안내")
entries = [dict(e, is_en_conversion=False),
           {"type": "law", "lang": "west", "title": "Reading Culture Promotion Act", "report_no": "Act No. 21447",
            "raw": "Reading Culture Promotion Act. Act No. 21447.", "is_en_conversion": True}]
ok(main._pair_manuscript_conversions(entries) == {0: 1}, "원고의 영문 변환 법령 항목을 공포번호로 짝지음")

if "--live" in sys.argv:
    print("[4] 실제 API")
    import httpx
    verify_kr._get = verify_kr.__dict__["_get"]  # 위에서 바꾼 가짜를 되돌린다
    import importlib
    importlib.reload(verify_kr)
    with httpx.Client() as c:
        r = verify_kr.law_search(c, "독서문화진흥법")
        ok(r and r["name_en"] == "Reading Culture Promotion Act", f"실제 조회: {r and r['no_label']} · {r and r['name_en']}")

print(f"\n전체 {_PASS}건 통과")
