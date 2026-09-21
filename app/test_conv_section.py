# -*- coding: utf-8 -*-
"""영문 변환 소절 표제 인식 테스트 (2026.09.07-01).

'국한문 참고문헌의 영문 표기' 류 소절 표제 감지 → is_en_conversion 플래그 전달 →
crosscheck 우선 사용 → 배열·그룹핑 → 영문 변환 목록 재활용까지.
실행: python app/test_conv_section.py  (AI 미사용 — 네트워크·API 키 불필요)
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949) 대비

sys.path.insert(0, str(Path(__file__).parent))

import extract
import crosscheck
import formatter

_PASS = 0


def ok(cond, label):
    global _PASS
    assert cond, f"실패: {label}"
    _PASS += 1
    print(f"  ✓ {label}")


# ---------------------------------------------------------------- 1) 표제 감지
print("[1] 소절 표제 패턴")
HEADINGS = [
    "국한문 참고문헌의 영문 표기",
    "국문 참고문헌의 영문 표기(English translation / Romanization of references originally written in Korean)",
    "국문 참고문헌의 영어 표기 (알파벳순)",
    "영문 변환 목록",
    "[국문 참고문헌 영문 변환 목록]",
    "영문화 목록",
    "(English translation / Romanization of references originally written in Korean)",
    "• 국한문참고문헌의 영문표기 :",
    "<참고문헌의 영문 표기>",
]
for h in HEADINGS:
    ok(extract._is_conv_heading_line(h), f"표제 인식: {h[:44]}")

NOT_HEADINGS = [
    # 연도가 있는 문헌 항목(제목에 '영문 표기'가 들어가도 표제가 아니다)
    "김철수 (2020). 국문 학술지의 영문 표기 실태. 도서관학, 51(3), 1-20.",
    # 줄바꿈으로 잘린 제목 조각 — 표제 문구 뒤에 다른 문장이 이어진다
    "영문 표기 실태와 개선 방안. 도서관학논집, 12, 1-20",
    # 괄호 없는 영문 줄 — 문헌 제목의 줄바꿈 조각과 구분 불가라 표제로 안 본다
    "Romanization of Korean bibliographic references.",
    # 서양문헌 그룹 표제(변환 목록이 아님)
    "영문 참고문헌",
    "국문 참고문헌",
    "영문 초록",
]
for h in NOT_HEADINGS:
    ok(not extract._is_conv_heading_line(h), f"오인 없음: {h[:44]}")

# ---------------------------------------------------------------- 2) 구역 분할
print("[2] find_en_conversion_split")
SECTION = """김성준 (2024). 학교도서관 현황조사 체계 분석. 한국도서관·정보학회지, 55(1), 1-25. https://doi.org/10.16981/kliss.55.1.202403.1
김철수 (2023). 도서관 통계의 신뢰성 연구. 정보관리학회지, 40(2), 55-78.
이용재 외 (2022). 학교도서관 운영 실태. 부산: 도서출판 미래.
국립중앙도서관 (2025). 전국 도서관 통계조사 보고서. 서울: 국립중앙도서관.
Smith, J. (2020). School library statistics. Library Quarterly, 90(4), 431-450.

국한문 참고문헌의 영문 표기
(English translation / Romanization of references originally written in Korean)

Kim, Sungjun (2024). An analysis of the school library survey system. Journal of Korean Library and Information Science Society, 55(1), 1-25. https://doi.org/10.16981/kliss.55.1.202403.1
Kim, Cheolsu (2023). A study on the reliability of library statistics. Journal of the Korean Society for Information Management, 40(2), 55-78.
Lee, Yongjae et al. (2022). School Library Management Practices. Busan: Mirae Publishing.
The National Library of Korea (2025). National Library Statistics Survey Report. Seoul: The National Library of Korea."""

main_sec, conv_sec, head = extract.find_en_conversion_split(SECTION)
ok(head == "국한문 참고문헌의 영문 표기", f"표제 문구 추출: {head}")
ok("Smith, J." in main_sec and "Kim, Sungjun" not in main_sec, "일반 목록에 원문만 남음")
ok(conv_sec.startswith("Kim, Sungjun") and "English translation" not in conv_sec,
   "변환 목록에서 영문 부기 줄 제거")
main_raws = extract.split_entries(main_sec)
conv_raws = extract.split_entries(conv_sec)
ok(len(main_raws) == 5, f"일반 목록 5건 분리 (실제 {len(main_raws)})")
ok(len(conv_raws) == 4, f"변환 목록 4건 분리 (실제 {len(conv_raws)})")

# 표제가 없는 구역은 그대로 반환
same, empty, no_head = extract.find_en_conversion_split("홍길동 (2020). 제목. 학회지, 1(1), 1-10.")
ok(empty == "" and no_head == "", "표제 없는 구역은 분할하지 않음")

# split_entries 단독 사용 시에도 표제 줄이 직전 항목에 붙지 않는다
glued = extract.split_entries(
    "이용재 (2022). 학교도서관 운영 실태. 부산: 도서출판 미래.\n국한문 참고문헌의 영문 표기\n"
    "Kim, Sungjun (2024). An analysis. Journal of KLISS, 55(1), 1-25.")
ok(all("영문 표기" not in r for r in glued), "split_entries가 표제 줄을 항목에서 배제")

# ---------------------------------------------------------------- 3) 플래그 우선
print("[3] crosscheck 플래그 우선")
e_ko = {"lang": "ko", "authors": ["김성준"], "year": "2024", "title": "학교도서관 현황조사 체계 분석",
        "doi": "10.16981/kliss.55.1.202403.1", "type": "journal", "is_en_conversion": False}
e_conv = {"lang": "west", "authors": ["Kim, Sungjun"], "year": "2024", "title": "An analysis",
          "doi": "10.16981/kliss.55.1.202403.1", "type": "journal", "is_en_conversion": True}
e_west = {"lang": "west", "authors": ["Smith, J."], "year": "2020", "title": "School library statistics",
          "type": "journal", "is_en_conversion": False}
flags = crosscheck.en_conversion_flags([e_ko, e_west, e_conv])
ok(flags == [False, False, True], "명시 플래그를 그대로 사용")
ok(crosscheck.is_conversion_pair(e_ko, e_conv), "원문↔변환(플래그 상이)은 짝")
ok(not crosscheck.is_conversion_pair(e_conv, dict(e_conv)), "변환끼리는 짝 아님")
# 표제 밖 서양어 문헌(플래그 False)과 국문 문헌은 DOI가 같아도 휴리스틱으로 짝짓지 않는다
e_west_same_doi = dict(e_west, doi=e_ko["doi"])
ok(not crosscheck.is_conversion_pair(e_ko, e_west_same_doi),
   "명시 모드에서는 표제 밖 서양어 문헌을 변환으로 추정하지 않음")
# 플래그가 아예 없는 원고는 현행 휴리스틱 유지
h_ko = {k: v for k, v in e_ko.items() if k != "is_en_conversion"}
h_conv = {k: v for k, v in e_conv.items() if k != "is_en_conversion"}
ok(crosscheck.en_conversion_flags([h_ko, h_conv]) == [False, True], "플래그 없으면 휴리스틱")

# ---------------------------------------------------------------- 4) 배열
print("[4] formatter 배열")
order = formatter.sort_and_disambiguate([dict(e_conv), dict(e_west), dict(e_ko)])
ok([e.get("is_en_conversion") for e in order] == [False, False, True]
   and order[0]["lang"] == "ko" and order[1]["authors"] == ["Smith, J."],
   "국내→서양→(동양)→영문 변환 순으로 배열")

# ---------------------------------------------------------------- 5) 짝 매칭(엄격)
print("[5] 원고 병기 변환 짝 매칭")
import main as main_mod
E = [
    {"lang": "ko", "type": "journal", "year": "2024", "doi": "10.1/a", "volume": "55", "issue": "1",
     "pages": "1-25", "raw": "김성준…", "is_en_conversion": False},                    # 0 — DOI 짝
    {"lang": "ko", "type": "journal", "year": "2023", "doi": "", "volume": "40", "issue": "2",
     "pages": "55-78", "raw": "김철수…", "is_en_conversion": False},                   # 1 — 권호면수 짝
    {"lang": "ko", "type": "thesis", "year": "2021", "raw": "박사1…", "is_en_conversion": False},  # 2 — 동년 학위 2건: 모호
    {"lang": "ko", "type": "thesis", "year": "2021", "raw": "박사2…", "is_en_conversion": False},  # 3
    {"lang": "ko", "type": "book", "year": "2022", "raw": "이용재…", "is_en_conversion": False},   # 4 — 유형+연도 유일 짝
    {"lang": "west", "type": "journal", "year": "2024", "doi": "10.1/a", "volume": "55",
     "issue": "1", "pages": "1-25", "raw": "Kim, Sungjun…", "is_en_conversion": True},   # 5
    {"lang": "west", "type": "journal", "year": "2023", "doi": "", "volume": "40", "issue": "2",
     "pages": "55-78", "raw": "Kim, Cheolsu…", "is_en_conversion": True},                # 6
    {"lang": "west", "type": "thesis", "year": "2021", "raw": "Park…", "is_en_conversion": True},  # 7
    {"lang": "west", "type": "book", "year": "2022", "raw": "Lee…", "is_en_conversion": True},     # 8
]
pairs = main_mod._pair_manuscript_conversions(E)
ok(pairs.get(0) == 5, "DOI 일치로 짝")
ok(pairs.get(1) == 6, "연도+권·호·면수 일치로 짝")
ok(pairs.get(4) == 8, "유형+연도 유일성으로 짝")
ok(2 not in pairs and 3 not in pairs, "같은 해 학위논문 2건(모호)은 짝짓지 않음")

# ---------------------------------------------------------------- 6) 파이프라인 종단
print("[6] 파이프라인 (규칙 모드, AI·네트워크 없음)")
import aiengine
aiengine.is_configured = lambda: False  # AI 키가 있어도 규칙 모드로 강제

MANUSCRIPT = """학교도서관 현황조사 체계 분석 연구

Ⅰ. 서론
학교도서관 현황조사의 체계를 분석하였다(김성준, 2024). 도서관 통계의 신뢰성 문제는 김철수(2023)가 지적한 바 있다.
해외에서는 Smith(2020)의 연구가 대표적이다. 국립중앙도서관(2025)의 조사와 이용재 외(2022)의 연구도 참고하였다.

Ⅱ. 결론
이상의 논의를 종합하였다.

참고문헌

""" + SECTION

res = main_mod._process_file("테스트_학교도서관.txt", MANUSCRIPT.encode("utf-8"),
                             {"style_id": "munpyeonhyeop", "verify": False,
                              "crosscheck": True, "english": True}, lambda *a: None)
ok(not res.get("error"), f"오류 없음 (error={res.get('error')})")
groups = []
for it in res["items"]:
    if it["group"] not in groups:
        groups.append(it["group"])
ok(groups == ["국내문헌", "서양문헌", "국문 문헌의 영문 변환 표기"],
   f"그룹 순서: {' → '.join(groups)}")
n_conv = sum(1 for it in res["items"] if it["group"] == "국문 문헌의 영문 변환 표기")
ok(n_conv == 4, f"변환 그룹 4건 (실제 {n_conv})")
ok(any("소절을 인식" in w for w in res["warnings"]), "소절 인식 안내 표시")
ok(res.get("english_list") and len(res["english_list"]) == 4,
   f"영문 변환 목록: 원고 표기 4건 재활용 (실제 {len(res.get('english_list') or [])})")
ok(any("재활용" in w for w in res["warnings"]), "재활용 안내 표시")
cc = res.get("crosscheck") or {}
uncited = [c.get("raw", "") + c.get("authors", "") for c in cc.get("listed_not_cited", [])]
ok(not any("Kim" in u or "Lee" in u or "National Library" in u for u in uncited),
   f"변환 항목이 '본문 미인용'으로 잡히지 않음 (미인용 {len(uncited)}건)")
ok(res["health"].get("en_conversions") == 4, "건전성 리포트 변환 건수 분리 집계")
ok(res["health"].get("year_dist", {}).get("2024", 0) == 1, "연도 분포에 변환 이중 집계 없음")

# ---------------------------------------------------------------- 7) 저자 전체 이름·배열·HWPX 대비 표시 (2026.09.11-01)
print("[7] 영문 변환 저자 전체 이름 · 알파벳 배열 · HWPX 원고 대비 표시 · 목록 한 번만")
import hwpx_export
import report
import zipfile
import io

# 이니셜 금지 — 성씨 목록에 없는 성(천 Cheon)도 변환 항목이면 전체 이름 유지
ok(formatter.format_authors({"authors": ["Cheon, Gyeongrok", "Jo, Yong-gu"], "lang": "west",
                             "is_en_conversion": True}) == "Cheon, Gyeongrok & Jo, Yong-gu",
   "변환 항목 저자는 성씨 목록과 무관하게 전체 이름 (Cheon, Gyeongrok & Jo, Yong-gu)")
ok(formatter.format_authors({"authors": ["Cheon, Gyeongrok"], "lang": "west"}) == "Cheon, Gyeongrok",
   "서양문헌 속 한국인 저자(Cheon) — 보강된 성씨 목록으로 전체 이름 유지")
ok(formatter.format_authors({"authors": ["Smith, John", "Doe, Jane"], "lang": "west"}) == "Smith, J. & Doe, J.",
   "서양 저자는 여전히 두문자")


def _e(authors, year, title, lang, conv=True, type_="journal"):
    return {"authors": authors, "year": year, "title": title, "lang": lang,
            "is_en_conversion": conv, "type": type_}


# AI가 변환 항목에 lang=ko/west를 섞어 달아도 알파벳순이어야 한다(AASL·법령이 Yang 뒤로 밀리던 문제)
mixed = [
    _e(["Yang, Sooyeon", "Park, Seong Seog"], "2020", "A Study", "ko"),
    _e(["American Association of School Librarians"], "2012", "School Libraries Count!", "west", type_="web"),
    _e([], "", "Reading Culture Promotion Act", "west", type_="law"),
    _e([], "", "School Library Promotion Act", "west", type_="law"),
    _e(["Yun, Junchae", "Seo, Hyeok"], "2010", "A Study 2", "west"),
    _e(["Pyeon, Jiyun"], "2025", "Development", "ko"),
    _e(["Sin, Insu"], "2025", "Predicting", "ko"),
    _e(["Kim, Sunmi"], "2018", "Development", "ko"),
    _e(["Kim, Hye Jeong"], "2021", "A survey", "ko"),
    _e(["American Association of School Librarians"], "2012", "School Libraries Count!", "west",
       conv=False, type_="web"),
]
order = [(e.get("authors") or [e["title"]])[0] for e in formatter.sort_and_disambiguate(mixed)]
ok(order[1:] == ["American Association of School Librarians", "Kim, Hye Jeong", "Kim, Sunmi",
                 "Pyeon, Jiyun", "Reading Culture Promotion Act", "School Library Promotion Act",
                 "Sin, Insu", "Yang, Sooyeon", "Yun, Junchae"],
   f"변환 그룹 알파벳순(lang 혼재·법령 제목·같은 성은 이름순): {' → '.join(o.split(',')[0] for o in order[1:])}")
ok(all(e["year"] == "2012" for e in mixed if e.get("type") == "web"),
   "서양문헌 원문과 변환 목록의 같은 단체 저자에 2012a/b를 붙이지 않음")

# HWPX 원고 대비 — 어절 단위, 삭제는 다음 어절에 표시, 원고에 없던 항목은 통째로
segs = hwpx_export.diff_segments(
    "Cheon, Gyeongrok, & Jo, Yong-gu (2025). A Case Study. Korean Language Education, 189, 139-160.",
    "Cheon, Gyeongrok & Jo, Yong-gu (2025). A Case Study. Korean Language Education, 189, 139-160. https://doi.org/10.29401/KLE.189.4")
ok([t.strip() for t, m in segs if m] == ["Gyeongrok", "https://doi.org/10.29401/KLE.189.4"],
   "바뀐 어절(쉼표 뺀 Gyeongrok)과 새로 넣은 DOI만 표시")
segs = hwpx_export.diff_segments("경기도교육청 (2024). 결과. 출처: https://x", "경기도교육청 (2024). 결과. https://x")
ok([t.strip() for t, m in segs if m] == ["https://x"], "원고에서 뺀 '출처:'는 다음 어절(주소)에 표시")
ok(hwpx_export.diff_segments("", "Baek, Wongeun (2024). New.") == [("Baek, Wongeun (2024). New.", True)],
   "원고에 없던 항목은 통째로 표시")

res_h = {
    "filename": "테스트.hwp", "style_name": "문편협 공통기준 (기본)", "checked_at": "2026-09-11 15:02",
    "app_version": "test",
    "items": [
        {"group": "국내문헌", "raw": "천경록, 조용구 (2025). 초등학생용 독서 능력 검사의 동등화 사례 연구. 국어교육, 189, 139-160.",
         "formatted": "천경록, 조용구 (2025). 초등학생용 독서 능력 검사의 동등화 사례 연구. 국어교육, 189, 139-160. https://doi.org/10.29401/KLE.189.4",
         "changed": True},
        {"group": "국내문헌", "raw": "박주현 (2016). 아동의 독서태도 검사도구 개발. 한국도서관·정보학회지, 47(2), 329-358.",
         "formatted": "박주현 (2016). 아동의 독서태도 검사도구 개발. 한국도서관·정보학회지, 47(2), 329-358.", "changed": False},
        {"group": "국문 문헌의 영문 변환 표기", "raw": "Cheon, Gyeongrok, & Jo, Yong-gu (2025). A Case Study.",
         "formatted": "Cheon, Gyeongrok & Jo, Yong-gu (2025). A Case Study.", "changed": True},
    ],
    "english_list": ["Cheon, Gyeongrok & Jo, Yong-gu (2025). A Case Study.", "Park, Juhyeon (2016). Development."],
    "english_items": [
        {"formatted": "Cheon, Gyeongrok & Jo, Yong-gu (2025). A Case Study.",
         "raw": "Cheon, Gyeongrok, & Jo, Yong-gu (2025). A Case Study."},
        {"formatted": "Park, Juhyeon (2016). Development.", "raw": ""},
    ],
}


def _sec(data: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode("utf-8")


sec = _sec(hwpx_export.build_result_hwpx(res_h))
ok(sec.count("[국문 문헌의 영문 변환 표기]") == 0 and sec.count("[국문 참고문헌 영문 변환 목록]") == 1,
   "영문 변환 목록이 있으면 원고의 변환 항목 그룹은 싣지 않아 목록이 한 번만")
ok('charPrIDRef="50"><hp:t>https://doi.org/10.29401/KLE.189.4' in sec, "새로 넣은 DOI가 빨간색 run")
ok('charPrIDRef="50"><hp:t>Park, Juhyeon (2016). Development.' in sec, "원고에 없던 변환 항목은 통째로 빨간색")
n_red = sec.count('charPrIDRef="50"')
# 쉼표만 빠진 변환 항목(', &' → ' &')은 대비표와 같은 기준(구두점·공백만 다르면 변경 없음)으로 표시하지 않는다
ok(n_red == 2, f"빨간색 run 2개(DOI·신규 항목) — 구두점만 다른 항목은 표시 없음 (실제 {n_red})")
ok("빨간색 글자 = 올린 원고와 달라진 부분" in sec, "범례 한 줄")
sec0 = _sec(hwpx_export.build_result_hwpx(res_h, marks=False))
ok('charPrIDRef="50"' not in sec0 and "빨간색 글자" not in sec0, "marks=False면 표시·범례 없음")
prv = zipfile.ZipFile(io.BytesIO(hwpx_export.build_result_hwpx(res_h))).read("Preview/PrvText.txt").decode("utf-8")
ok("Cheon, Gyeongrok & Jo, Yong-gu (2025). A Case Study." in prv.splitlines(),
   "미리보기 텍스트는 run이 나뉘어도 한 줄")
txt = report.build_result_txt(res_h)
ok(txt.count("[국문 문헌의 영문 변환 표기]") == 0 and txt.count("Cheon, Gyeongrok & Jo") == 1,
   "TXT도 변환 목록을 한 번만")

# AI 영문 변환 줄 정규화 — 2인 '&' 앞 쉼표 제거, 3인 이상 유지, 법령 구두점
ok(formatter.normalize_en_line("Kim, Hye Jeong, & Heo, Moah (2021). A survey. Reading Research, 59, 9-50.")
   == "Kim, Hye Jeong & Heo, Moah (2021). A survey. Reading Research, 59, 9-50.", "2인 저자 ', &' → ' &'")
ok(formatter.normalize_en_line("Han, Cheolwoo, Lee, Kyounghwa, & Choi, Kyuhong (2007). The study. J, 18, 1-2.")
   == "Han, Cheolwoo, Lee, Kyounghwa, & Choi, Kyuhong (2007). The study. J, 18, 1-2.", "3인 이상은 ', &' 유지")
ok(formatter.normalize_en_line("School Library Promotion Act, Act No. 18547.")
   == "School Library Promotion Act. Act No. 18547.", "법령 'Act No.' 앞 쉼표 → 마침표")
# 저자가 붙인 a/b 부기는 제목순으로 뒤집지 않는다(2025b가 2025a 앞에 오던 문제)
ab = [{"authors": ["全国学校図書館協議会"], "year": "2025b", "title": "2025年度 学校図書館調査の結果", "lang": "east"},
      {"authors": ["全国学校図書館協議会"], "year": "2025a", "title": "第70回 学校読書調査の結果", "lang": "east"}]
ok([e["year"] for e in formatter.sort_and_disambiguate(ab)] == ["2025a", "2025b"], "a/b 부기 순서대로 배열")

# ---------------------------------------------------------------- 8) KCI 통권 번호 (2026.09.11-03)
print("[8] KCI가 통권 번호를 <issue>에 싣는 학술지 — 호 오제안 방지")
import verify
kci = {"title": "중학교 1∼3학년 읽기 능력 검사 도구 개발 및 IRT 분석을 통한 타당화 연구", "container": "국어교육",
       "year": "2020", "volume": "", "issue": "170", "pages": "81-122", "doi": "10.29401/KLE.170.3", "source": "KCI"}
e_vol = {"type": "journal", "title": kci["title"], "year": "2020", "volume": "170", "issue": "", "pages": "81-122"}
m = verify._meta_kr_for_entry(e_vol, kci)
ok(m["volume"] == "170" and m["issue"] == "", "원고가 호 없이 권만 적었으면 KCI의 호(170)를 권으로 옮김")
import main as main_mod2
ok(main_mod2._build_suggestions(e_vol, m) == [], "'호 (없음)→170' 제안이 나오지 않음(국어교육 170 실측)")
e_iss = dict(e_vol, issue="3")
m2 = verify._meta_kr_for_entry(e_iss, kci)
ok(m2["issue"] == "170" and m2["volume"] == "", "원고가 호를 적었으면 KCI 값 그대로 대조")

# ---------------------------------------------------------------- 9) 부제 소문자·카카오 책 (2026.09.18-01)
print("[9] 콜론 뒤 부제 첫 낱말 소문자 — 학회 오류유형 안내 1.2·공통기준 Wilson 예시")
sc = formatter.sentence_case
ok(sc("Why school librarians matter: What years of research tell us")
   == "Why school librarians matter: what years of research tell us", "APA식 'What' → 'what'(원고가 이미 문장식이어도)")
ok(sc("Digital Library Research: Current Developments And Trends")
   == "Digital library research: current developments and trends", "Title Case 원고 → 부제까지 소문자")
ok(sc("School libraries: SNS use in Korea") == "School libraries: SNS use in Korea", "콜론 뒤 약어(SNS)는 유지")
ok(sc("Who cares? A study of libraries") == "Who cares? A study of libraries", "물음표 뒤는 새 문장 — 대문자 유지")
ok(sc("Time series: iPhone use") == "Time series: iPhone use", "내부 대문자(iPhone) 유지")

print("[9] 카카오 책 검색 — 레코드 변환·판 선택·저자 불일치 (응답은 가짜, 네트워크 없음)")
import verify_kr
_docs = [{"title": "디지털도서관 운영론", "authors": ["이수상"], "publisher": "한국도서관협회",
          "datetime": "2026-02-01T00:00:00.000+09:00", "isbn": "8976781147 9788976781147", "url": "https://search.daum.net/x"},
         {"title": "디지털도서관 운영론", "authors": ["이수상"], "publisher": "한국도서관협회",
          "datetime": "2008-08-25T00:00:00.000+09:00", "isbn": "9788976780000", "url": "https://search.daum.net/y"}]
_orig_docs, _orig_env = verify_kr._kakao_docs, verify_kr.env_get
verify_kr._kakao_docs = lambda client, params: list(_docs)
verify_kr.env_get = lambda k: "test-key" if k == "KAKAO_REST_API_KEY" else _orig_env(k)
try:
    r = verify_kr.kakao_book_search(None, "디지털도서관운영론", "이수상", "2008")
    ok(r and r["source"] == "카카오 책" and r["year"] == "2008" and r["isbn"] == "9788976780000",
       "같은 서명의 판이 둘이면 원고 연도(2008)와 맞는 판을 고름")
    ok(r["url"] == "https://search.daum.net/y" and r["publisher"] == "한국도서관협회", "Daum 책 링크·출판사 전달")
    r2 = verify_kr.kakao_book_search(None, "디지털도서관운영론", "홍길동", "2008")
    ok(r2 and r2.get("author_mismatch"), "저자가 다르면 author_mismatch — 동명 서명을 '확인'하지 않음")
    ok(verify_kr.kakao_book_search(None, "전혀 다른 책 제목입니다", "", "") is None, "유사도 0.80 미만이면 None")
    r3 = verify_kr.kakao_book_by_isbn(None, "978-89-7678-1147")
    ok(r3 and r3["isbn"] == "9788976781147" and r3["sim"] == 1.0, "ISBN 조회 — 'ISBN10 ISBN13' 중 13자리를 취함")
    ok(verify_kr.kr_api_status()["kakao"] is True, "kr_api_status에 kakao 상태")
    r4 = verify_kr.kakao_book_search(None, "디지털도서관운영론", "이수상", "2003")
    ok(r4 and r4["year"] == "" and r4["publisher"] == "" and "2026년판만 수록" in r4["note"] or "2008년판만 수록" in r4["note"],
       "원고 연도의 판이 없으면 실존만 확인 — 연도·출판사 비워 다른 판으로 교정 제안하지 않음")
finally:
    verify_kr._kakao_docs, verify_kr.env_get = _orig_docs, _orig_env

# ---------------------------------------------------------------- 10) 해외 DB에 없는 DOI·표기 언어만 다른 제목 (2026.09.20-02)
print("[10] DOI가 Crossref에 없거나 제목 언어가 달라도 권호·면수로 같은 문헌 확인")
ok(verify._biblio_agrees({"year": "2024", "volume": "9", "issue": "6", "pages": "107-114"},
                         {"year": "2024", "volume": "9", "issue": "6", "pages": "107-114"}), "연도·권·호·첫 면 일치 → 같은 문헌")
ok(verify._biblio_agrees({"year": "2024", "volume": "9", "pages": "107-114"},
                         {"year": "2024", "volume": "9", "issue": "6", "pages": "107"}), "원고에 호가 없어도 있는 요소끼리 일치")
ok(not verify._biblio_agrees({"year": "2024", "volume": "9", "issue": "6", "pages": "107-114"},
                             {"year": "2024", "volume": "9", "issue": "6", "pages": "201-210"}), "첫 면이 다르면 다른 문헌")
ok(not verify._biblio_agrees({"year": "2024"}, {"year": "2024"}), "연도 하나만으로는 판단하지 않음")
e_doi = {"type": "journal", "title": "다문화청소년의 문화적응스트레스와 삶의 만족도 간의 관계", "doi": "10.9708/jksci.2022.27.04.999",
         "year": "2022", "volume": "27", "issue": "4", "pages": "119-125"}
m_doi = {"source": "KCI", "doi": "10.9708/jksci.2022.27.04.119", "year": "2022", "volume": "27", "issue": "4", "pages": "119-125"}
sg = main_mod2._build_suggestions(e_doi, m_doi)
ok(len(sg) == 1 and sg[0]["field"] == "doi" and sg[0]["suggested"].endswith(".119"), "원고 DOI ≠ KCI 등록 DOI → DOI 교정 제안")
ok(main_mod2._build_suggestions(dict(e_doi, doi=""), m_doi) == [], "원고에 DOI가 없으면 넣으라고 하지 않음(확인될 경우에만 기입)")
ok(main_mod2._build_suggestions(dict(e_doi, doi="10.9708/JKSCI.2022.27.04.119"), m_doi) == [], "대소문자만 다른 DOI는 같은 DOI")

# ---------------------------------------------------------------- 11) 통권 번호가 호에 들어간 항목 (2026.09.21-01)
print("[11] 권 없이 통권 번호만 있는 학술지 — 호에 들어가도 번호가 사라지지 않음")
e_t = {"type": "journal", "lang": "ko", "authors": ["양수연", "박성석", "민병곤"], "year": "2020",
       "title": "중학교 1~3학년 읽기 능력 검사 도구 개발 및 IRT 분석을 통한 타당화 연구", "container": "국어교육",
       "volume": "", "issue": "170", "pages": "81-122", "doi": "10.29401/KLE.170.3",
       "raw": "양수연, 박성석, 민병곤 (2020). 중학교 1~3학년 읽기 능력 검사 도구 개발 및 IRT 분석을 통한 타당화 연구. 국어교육, 170, 81-122."}
ok(formatter.format_entry(dict(e_t)).endswith("국어교육, 170, 81-122. https://doi.org/10.29401/KLE.170.3"),
   "형식 변환: 호에만 있는 번호를 권 자리에 — '국어교육, 81-122.'로 사라지던 문제(2026-09-21 신고)")
import rules as rules_mod
n = rules_mod.backfill_from_raw(dict(e_t))
ok(n["volume"] == "170" and n["issue"] == "", "구조화 뒤 정규화: 호→권으로 옮겨 KCI 대조·제안이 같은 자리를 봄")
ok(rules_mod.backfill_from_raw(dict(e_t, volume="54", issue="2"))["issue"] == "2", "권·호가 다 있으면 그대로")
r_t = rules_mod.structure_entry("양수연, 박성석, 민병곤 (2020). 중학교 1~3학년 읽기 능력 검사 도구 개발 및 IRT 분석을 통한 타당화 연구. 국어교육, 170, 81~122.")
ok("1~3학년" in r_t["title"] and r_t["pages"] == "81-122", "제목 속 '1~3학년'은 그대로, 면수 '81~122'만 붙임표로")

# ---------------------------------------------------------------- 12) 면수 1쪽 차이 (2026.09.21-02)
print("[12] 등록 면수와 원고가 1쪽 다르면 교정 제안 대신 발행본 확인 비고")
e_p = {"type": "journal", "title": "컴퓨터 적응 검사를 활용한 독서 능력 평가 시스템의 개발", "year": "2023",
       "volume": "76", "issue": "", "pages": "179-191", "doi": "10.22818/jeke.2023..76.180"}
m_p = {"source": "KCI", "year": "2023", "volume": "76", "issue": "", "pages": "180-191", "doi": "10.22818/jeke.2023..76.180"}
ok(main_mod2._build_suggestions(e_p, m_p) == [], "첫 면 1쪽 차이(179-191 ↔ KCI 180-191) → 교정 제안 없음 (조용구 2023 실측)")
note = main_mod2._page_check_note(e_p, m_p)
ok(len(note) == 1 and "발행본" in note[0] and "180-191" in note[0], "대신 '발행본 면수를 따르라' 비고")
ok(main_mod2._build_suggestions(e_p, dict(m_p, pages="181-191")) and not main_mod2._page_check_note(e_p, dict(m_p, pages="181-191")),
   "2쪽 이상 차이는 종전대로 등록 서지 기준 교정 제안")
ok(main_mod2._build_suggestions(e_p, dict(m_p, pages="179-192")) == [] and main_mod2._page_check_note(e_p, dict(m_p, pages="179-192")),
   "끝 면 1쪽 차이도 비고")
ok(main_mod2._page_check_note(e_p, dict(m_p, pages="179-191")) == [], "같으면 비고 없음")
ok(main_mod2._pages_off_by_one("179-91", "180-191"), "축약 면수 '179-91'도 179-191로 읽음")

# ---------------------------------------------------------------- 13) KCI 서지 검증 표시 N (2026.09.21-03)
print("[13] KCI 서지 검증 표시 N — 면수는 교정 근거로 쓰지 않고 원문 보유 정보원 확인 비고")
e_n = {"type": "journal", "title": "독서 능력 표준화 검사 도구의 연구 개발", "year": "2006", "volume": "15", "issue": "", "pages": "407-436"}
m_n = {"source": "KCI", "year": "2006", "volume": "15", "issue": "", "pages": "425-456", "kci_verified": "N"}
ok(main_mod2._build_suggestions(e_n, m_n) == [], "검증 N: 18쪽 차이여도 교정 제안 없음 (천경록 2006 실측 — 발행본·KISS 407-436)")
n13 = main_mod2._page_check_note(e_n, m_n)
ok(len(n13) == 1 and "검증 표시가 N" in n13[0] and "425-456" in n13[0], "비고에 KCI 등록값과 원문 보유 정보원 확인 안내")
ok(len(main_mod2._build_suggestions(e_n, dict(m_n, kci_verified="Y"))) == 1, "검증 Y이고 2쪽 이상 차이면 종전대로 제안")
ok(main_mod2._build_suggestions(dict(e_n, volume="14"), m_n) and main_mod2._build_suggestions(dict(e_n, volume="14"), m_n)[0]["field"] == "volume",
   "검증 N이어도 권·호 등 다른 요소는 제안(면수만 보류)")

print(f"\n전체 {_PASS}건 통과")
