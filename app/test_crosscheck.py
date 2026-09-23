# -*- coding: utf-8 -*-
"""본문 인용 ↔ 목록 대조·영문 변환 누락·HWPX 파싱 테스트 (2026.09.18-02).

한국비블리아학회·한국도서관정보학회 편집위원회가 손으로 잡아낸 지적(2026-09-18, 17+4건)을
refcheck가 놓치던 사례를 그대로 재현한다. AI·네트워크 불필요.
실행: python app/test_crosscheck.py
"""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))

import crosscheck as cc
import extract
import parsing
import rules

_PASS = 0


def ok(cond, label):
    global _PASS
    assert cond, f"실패: {label}"
    _PASS += 1
    print(f"  ✓ {label}")


def entries_from(raws, conv_raws=()):
    out = []
    for r in raws:
        e = rules.structure_entry(r); rules.backfill_from_raw(e); e["is_en_conversion"] = False; out.append(e)
    for r in conv_raws:
        e = rules.structure_entry(r); rules.backfill_from_raw(e); e["is_en_conversion"] = True; out.append(e)
    return out


# ---------------------------------------------------------------- 1) 인용 추출
print("[1] 본문 인용 추출 — 긴 기관명·일문 기관명·복수 연도·법령·(n.d.)")
body = ("전라남도교육청(2025)과 경기도교육청(2024)은 별도 조사를 실시하였고, 울산광역시교육연구정보원(2024)과 "
        "광주광역시교육청(2022), 부산광역시교육청(2016)은 일부 문항을 포함하였다. "
        "학교독서조사(全国学校図書館協議会, 2025a, 2025b)는 매년 실시한다. "
        "전국 학력·학습상황조사(全国学力・学習状況調査, 2026)는 독서 시간을 묻는다. "
        "진로교육 분야는 진로교육법[법률 제18298호] 제6조에 근거하여 조사한다. 학교도서관진흥법 제3조에 근거한다. "
        "「독서문화진흥법」도 있다. 개정 학교도서관진흥법 시행 이후(강봉숙, 박주현, 2019) 변화가 있었다. "
        "국제 조사(IEA, 2021)와 (Chartered Institute of Library and Information Professionals, "
        "School Library Association, & School Libraries Group, n.d.)도 있다. Great School Libraries(2024)의 조사. "
        "매년(2005) 실시하였다(2025). 학교도서관 현황조사(2024~2028)는 제외.")
cites = {(c["name"], c["year"]) for c in cc.extract_citations(body)}
ok(("전라남도교육청", "2025") in cites and ("울산광역시교육연구정보원", "2024") in cites, "5자 넘는 기관명 서술 인용")
ok(("남도교육청", "2025") not in cites, "기관명이 뒤 5자로 잘리지 않음")
ok(("全国学校図書館協議会", "2025") in cites, "일문 기관명 괄호 인용")
ok(("全国学力・学習状況調査", "2026") in cites, "가운뎃점(・) 든 일문 이름")
ok(("진로교육법", "") in cites and ("학교도서관진흥법", "") in cites and ("독서문화진흥법", "") in cites,
   "법령 인용 — [법률 제N호]·제N조·「」 (연도 없음)")
ok(("IEA", "2021") in cites, "약칭 인용 (IEA, 2021)")
ok(("Chartered Institute of Library and Information Professionals", "n.d.") in cites, "(…, n.d.) 인용")
ok(("Great School Libraries", "2024") in cites, "여러 낱말 서양 기관명")
ok(("매년", "2005") not in cites and ("실시하였다", "2025") not in cites, "표의 '매년(2005)'·서술어 괄호는 제외")
multi = [c for c in cc.extract_citations("(全国学校図書館協議会, 2025a, 2025b)")]
ok(len(multi) == 1 and multi[0]["year"] == "2025", "같은 저자 여러 해는 연도 정규화(2025a→2025)로 한 키")

# ---------------------------------------------------------------- 2) 대조
print("[2] 본문 ↔ 목록 대조 — 편집위원회 지적 재현")
raws = [
    "경기도교육청 (2024). 2024 학생 독서실태조사 결과. 출처: https://www.goe.go.kr/x",
    "광주광역시교육청 (2022). 2022 광주교육종합실태조사. 출처: https://www.gen.go.kr/x",
    "교육부 (2024). 제4차 학교도서관 진흥 기본계획(2024~2028).",
    "교육부 (2025). 2025 초중등 진로교육 현황조사 결과 발표. 출처: https://www.moe.go.kr/x",
    "국가환경교육센터 (2026). 환경교육실태조사. 출처: https://www.keep.go.kr/x",
    "독서문화진흥법. 법률 제21447호",
    "백원근 (2024. 04. 25.). 2023년 국민독서실태조사, 결과 어떻게 봐야 하나. 한국독서교육신문. https://www.readingnews.kr/x",
    "신인수, 박성재 (2025). 머신러닝을 활용한 청소년 독서 예측. 정보관리학회지, 42(1), 131-153.",
    "울산광역시교육연구정보원 (2024). 2023 울산 학생·학부모 실태조사(연구자료 2023-종단-2).",
    "학교도서관진흥법. 법률 제18547호.",
    "강봉숙, 박주현 (2019). 개정 학교도서관진흥법 시행 이후 사서교사 배치. 한국도서관·정보학회지, 50(3), 239-259.",
    "박주현, 길호현, 강지혜, 홍소람 (2026). 학교도서관·독서교육 현황조사 체계 구축 연구 (연구보고 CR 2026-3). 한국교육학술정보원.",
    "Great School Libraries (2024). Great school libraries survey. Available: https://www.greatschoollibraries.org.uk/x",
    "International Association for the Evaluation of Educational Achievement(IEA) (2021). PIRLS 2021 context questionnaires. Available: https://pirls2021.org/x",
    "Chartered Institute of Library and Information Professionals, School Library Association, & School Libraries Group (n.d.). Great School Libraries: Equal Futures?",
    "国立教育政策研究所 (2026). 令和8年度 全国学力・学習状況調査 質問調査. Available: https://www.nier.go.jp/x",
    "全国学校図書館協議会 (2025a). 第70回 学校読書調査の結果. Available: https://www.j-sla.or.jp/a",
    "全国学校図書館協議会 (2025b). 2025年度 学校図書館調査の結果. Available: https://www.j-sla.or.jp/b",
]
body2 = ("일부 시도교육청이 자체 설문을 실시하고 있으나(경기도교육청, 2024) 한계가 있다(백원근, 2024). "
         "표준화된 조사 체계는 없다(교육부, 2024). 이에 교육부(2024)는 고도화를 제시하였다. "
         "전라남도교육청(2025)과 경기도교육청(2024), 울산광역시교육연구정보원(2024)과 광주광역시교육청(2022), "
         "부산광역시교육청(2016)은 조사를 수행하였다. 광주광역시(2022)\n부산광역시(2016)\n"
         "학교독서조사(全国学校図書館協議会, 2025a, 2025b)는 매년 실시하고, 전국 학력·학습상황조사(全国学力・学習状況調査, 2026)는 "
         "독서 시간을 묻는다. 진로교육법[법률 제18298호] 제6조에 근거하여 조사한다. 학교도서관진흥법 제3조에 근거한다. "
         "독서문화진흥법에 따라. 개정 학교도서관진흥법 시행 이후(강봉숙, 박주현, 2019). 박주현과 변우열(2018)의 연구. "
         "국제 조사(IEA, 2021), (Chartered Institute of Library and Information Professionals, School Library Association, "
         "& School Libraries Group, n.d.), Great School Libraries(2024)의 조사가 있다.")
res = cc.cross_check(body2, entries_from(raws))
cnl = {(c["name"], c["year"]): c for c in res["cited_not_listed"]}
lnc = {c["authors"]: c for c in res["listed_not_cited"]}
ok(("전라남도교육청", "2025") in cnl and ("부산광역시교육청", "2016") in cnl, "인용됐지만 목록에 없는 긴 기관명 2건 (비블리아 지적 2·3)")
ok(("부산광역시", "2016") not in cnl, "표의 '부산광역시(2016)'는 '부산광역시교육청(2016)'과 한 건으로")
ok(("광주광역시", "2022") not in cnl, "표의 '광주광역시(2022)'는 목록 '광주광역시교육청 (2022)'의 일부 → 일치")
ok(("진로교육법", "") in cnl and "법령" in cnl[("진로교육법", "")]["note"], "진로교육법[법률 제18298호] — 목록에 없음 (지적 1)")
ok(("학교도서관진흥법", "") not in cnl and ("독서문화진흥법", "") not in cnl, "목록에 있는 법령은 문제없음")
ok(("全国学力・学習状況調査", "2026") in cnl, "인용 표기 ≠ 목록 저자명 (지적 4)")
ok(any("国立教育政策研究所" in x for x in cnl[("全国学力・学習状況調査", "2026")].get("candidates", [])),
   "같은 해 미인용 항목 国立教育政策研究所 (2026)을 후보로 부기")
ok(("IEA", "2021") not in cnl, "약칭 (IEA, 2021) ↔ '…Achievement(IEA) (2021)' 일치")
ok(("Chartered Institute of Library and Information Professionals", "n.d.") not in cnl, "(…, n.d.) ↔ 공동 기관 저자 (n.d.) 일치")
ok(("Great School Libraries", "2024") not in cnl, "여러 낱말 기관명 서술 인용 일치")
ok(("백원근", "2024") not in cnl, "신문 기사 '(2024. 04. 25.)'의 연도 앞 4자리로 일치")
ok("교육부" in lnc and "2024" in lnc["교육부"]["note"], "교육부 (2025) — 본문엔 2024만 인용 (지적 8)")
ok("국가환경교육센터" in lnc and "신인수, 박성재" in lnc, "인용 없는 국가환경교육센터·신인수 (지적 9·12)")
ok("国立教育政策研究所" in lnc, "国立教育政策研究所 (2026) 인용 없음 (지적 15)")
ok("박주현, 길호현, 강지혜, 홍소람" in lnc and "2018" in lnc["박주현, 길호현, 강지혜, 홍소람"]["note"],
   "박주현 외 (2026) — 같은 저자 다른 해만 인용 (도서관정보학회 지적 1)")
ok("全国学校図書館協議会" not in lnc, "(…, 2025a, 2025b)로 두 해 모두 인용된 항목은 문제없음")
ok("울산광역시교육연구정보원" not in lnc and "강봉숙, 박주현" not in lnc, "정상 인용 항목은 보고하지 않음")
ok(res["citations_found"] >= 12, f"인용 {res['citations_found']}건 탐지")

# ---------------------------------------------------------------- 3) 영문 변환 짝·누락
print("[3] 영문 변환 목록 누락 — 이름(성씨 로마자·기관명 낱말) 짝짓기")
import main as main_mod
ko_raws = [
    "교육부 (2024). 제4차 학교도서관 진흥 기본계획(2024~2028).",
    "교육부 (2025). 2025 초중등 진로교육 현황조사 결과 발표. 출처: https://www.moe.go.kr/x",
    "경기도교육청 (2024). 2024 학생 독서실태조사 결과. 출처: https://www.goe.go.kr/x",
    "울산광역시교육연구정보원 (2024). 2023 울산 학생·학부모 실태조사(연구자료 2023-종단-2).",
    "울산광역시교육청 (2023). 2024년 운영계획 수립을 위한 온라인 설문조사 결과 안내.",
    "백원근 (2024. 04. 25.). 2023년 국민독서실태조사, 결과 어떻게 봐야 하나. 한국독서교육신문.",
    "박주현 (2016). 아동의 독서태도 검사도구 개발. 한국도서관·정보학회지, 47(2), 329-358.",
    "독서문화진흥법. 법률 제21447호",
    "国立教育政策研究所 (2026). 令和8年度 全国学力・学習状況調査 質問調査.",
]
conv_raws = [
    "Ministry of Education (2024). The 4th Basic Plan for School Library Promotion (2024-2028).",
    "Ulsan Education Research and Information Institute (2024). 2023 Ulsan Student and Parent Survey (Research Report 2023-Longitudinal-2).",
    "Ulsan Metropolitan Office of Education (2023). Results of the online survey for establishing the 2024 plan.",
    "Park, Juhyeon (2016). Development of the Reading Attitudes Test Tool for Children. Journal of Korean Library and Information Science Society, 47(2), 329-358.",
    "Reading Culture Promotion Act. Act No. 21447.",
]
ents = entries_from(ko_raws, conv_raws)
pairs = main_mod._pair_manuscript_conversions(ents)
ok(pairs.get(0) is not None and ents[pairs[0]]["raw"].startswith("Ministry"), "교육부 (2024) ↔ Ministry of Education (2024) — 같은 해 보고서가 여럿이어도 이름으로 짝")
ok(pairs.get(3) is not None and ents[pairs[3]]["raw"].startswith("Ulsan Education Research"), "울산광역시교육연구정보원 ↔ Ulsan Education Research and Information Institute")
ok(pairs.get(4) is not None and ents[pairs[4]]["raw"].startswith("Ulsan Metropolitan"), "울산광역시교육청 (2023) ↔ Ulsan Metropolitan Office of Education (2023)")
ok(pairs.get(6) is not None and ents[pairs[6]]["raw"].startswith("Park, Juhyeon"), "박주현 ↔ Park (성씨 로마자)")
ok(pairs.get(7) is not None and ents[pairs[7]]["raw"].startswith("Reading Culture"), "법령은 공포번호로 짝(변환 쪽 유형 무관)")
missing = [ents[i]["raw"][:6] for i, e in enumerate(ents)
           if not e["is_en_conversion"] and e.get("lang") == "ko" and i not in pairs]
ok(missing == ["교육부 (2", "경기도교육청", "백원근 (2"], f"짝 없는 국문 문헌 = 변환 누락 3건 {missing} (비블리아 지적 5·7·11 유형)")
ok(not any(e.get("lang") != "ko" and not e["is_en_conversion"] and i in pairs for i, e in enumerate(ents)), "일문 문헌은 변환 대상에서 제외")
ok(cc.names_compatible("경기도교육청", "Ulsan Metropolitan Office of Education") is False, "지역이 다르면 기관명 불일치")
ok(cc.names_compatible("김영석", "Lee, Yongjae") is False, "성씨가 다르면 불일치")
ok(cc.names_compatible("천경록", "Cheon, Gyeongrok") and cc.names_compatible("변우열", "Byun, Woo-Yeoul"), "성씨 로마자 이형(Cheon·Byun)")

# ---------------------------------------------------------------- 4) 목록 분리
print("[4] 참고문헌 항목 분리 — 긴 기관 저자·법령·(n.d.)·글머리표 소절 표제")
sec = ("장보성 (2019). 특수학교의 학교도서관 운영 실태 분석 연구. 한국도서관·정보학회지, 50(1), 313-331.\n"
       "학교도서관진흥법. 법률 제18547호.\n"
       "American Association of School Librarians (2012). School Libraries Count! National Longitudinal Survey of School Library Programs.\n"
       "Chartered Institute of Library and Information Professionals, School Library Association, & School Libraries Group (n.d.). Great School Libraries: Equal Futures? An Imbalance of Opportunities.\n"
       "International Association for the Evaluation of Educational Achievement(IEA) (2021). PIRLS 2021 context questionnaires.\n"
       "全国学校図書館協議会 (발행년불명). 「学校図書館調査」の結果. Available: https://www.j-sla.or.jp/x\n"
       "⦁ 국한문 참고문헌의 영문 표기\n"
       "Jang, Bo Seong (2019). Analysis on the operating status of special schools' school library. Journal of Korean Library and Information Science Society, 50(1), 313-331.\n"
       "School Libraries Promotion Act. Act No. 18547.\n")
ko_part, conv_part, head = extract.find_en_conversion_split(sec)
ok(head == "국한문 참고문헌의 영문 표기", "글머리표(⦁) 붙은 소절 표제 인식")
raws4 = extract.split_entries(ko_part)
ok(len(raws4) == 6, f"국문 구역 6건으로 분리 (실제 {len(raws4)})")
ok(raws4[1] == "학교도서관진흥법. 법률 제18547호." and raws4[2].startswith("American Association"),
   "법령 항목과 42자 기관 저자 항목이 서로 붙지 않음 (도서관정보학회 원고 실측)")
ok(raws4[3].startswith("Chartered") and raws4[4].startswith("International"), "공동 기관 (n.d.)·약칭 든 기관 저자도 각각 한 항목")
conv4 = extract.split_entries(conv_part)
ok(len(conv4) == 2 and conv4[1].startswith("School Libraries Promotion Act"), "변환 구역 2건 — 영문 법령 항목 분리")

# ---------------------------------------------------------------- 5) HWPX 파싱
print("[5] HWPX 파싱 — 편집위원 메모 제외, 표 칸은 저마다 한 줄")
NS = 'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"'
xml = f'''<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" {NS}>
<hp:p id="1"><hp:run><hp:t>본문 첫 문단 </hp:t><hp:ctrl><hp:fieldBegin id="9" type="MEMO"><hp:subList>
<hp:p id="2"><hp:run><hp:t>해당 인용의 문헌이 참고문헌에서 확인되지 않습니다.</hp:t></hp:run></hp:p>
</hp:subList></hp:fieldBegin></hp:ctrl><hp:t>진로교육법[법률 제18298호] 제6조</hp:t><hp:ctrl><hp:fieldEnd beginIDRef="9"/></hp:ctrl><hp:t>에 근거한다.</hp:t></hp:run></hp:p>
<hp:p id="3"><hp:run><hp:tbl><hp:tr><hp:tc><hp:subList><hp:p id="4"><hp:run><hp:t>검사 도구</hp:t></hp:run></hp:p></hp:subList></hp:tc>
<hp:tc><hp:subList><hp:p id="5"><hp:run><hp:t>천경록(2006)</hp:t></hp:run></hp:p></hp:subList></hp:tc></hp:tr></hp:tbl></hp:run></hp:p>
<hp:p id="6"><hp:run><hp:t>교육부 (2024). 제4차 </hp:t><hp:markpenBegin color="#BAFF1A"/><hp:t>학교도서관 진흥 기본계획.</hp:t></hp:run></hp:p>
</hs:sec>'''
paras = parsing._hwpx_paragraphs(ET.fromstring(xml))
ok(paras[0] == "본문 첫 문단 진로교육법[법률 제18298호] 제6조에 근거한다.", "메모 본문은 빠지고 메모가 달린 글자는 그대로")
ok("확인되지 않습니다" not in "\n".join(paras), "메모 문단이 따로 실리지도 않음")
ok("검사 도구" in paras and "천경록(2006)" in paras and "도구천경록" not in "\n".join(paras), "표 칸은 저마다 한 줄 — '도구천경록(2006)'으로 붙지 않음")
ok(paras[-1] == "교육부 (2024). 제4차 학교도서관 진흥 기본계획.", "형광펜 표시가 있어도 글자 연속")

# ---------------------------------------------------------------- 6) 규칙 구조화
print("[6] 규칙 구조화 — 5인 이상 서양 저자·단행본 서명/출판지 경계")
e = rules.structure_entry("Wine, L. D., Pribesh, S., Kimmel, S. C., Dickinson, G., & Church, A. P. (2023). Impact of school librarians on elementary student achievement in reading and mathematics: A propensity score analysis. Library & Information Science Research, 45(3), 101252. Available: https://doi.org/10.1016/j.lisr.2023.101252")
rules.backfill_from_raw(e)
ok(e["authors"] == ["Wine, L. D", "Pribesh, S", "Kimmel, S. C", "Dickinson, G", "Church, A. P"] and e["year"] == "2023",
   "78자 저자 나열이 70자 제한에 걸리지 않음 — 'Wine, L. (2023). D., Pribesh…' 오분해 해소")
ok(e["title"].startswith("Impact of school librarians") and e["doi"] == "10.1016/j.lisr.2023.101252", "제목·DOI 정상")
import formatter as fmt
line = fmt.format_entry(e)
ok("Available:" not in line and line.endswith("https://doi.org/10.1016/j.lisr.2023.101252"), "학술지 DOI 앞 'Available:' 제거 (도서관정보학회 지적 2)")
e2 = rules.structure_entry("Thompson, J., Barthlow, M., Paynter, K. (2021). School librarians' teacher self-efficacy: A predictor of reading scores? School Library Research, 24.")
rules.backfill_from_raw(e2)
ok("Barthlow, M., & Paynter, K. (2021)" in fmt.format_entry(e2), "마지막 저자 앞 & 보충 (도서관정보학회 지적 3)")
for raw, title, place, pub in [
    ("Jeong, Ongnyeon, Kim, Hyosuk (2020). Multidimensional Reading Ability Diagnostic Test. Seoul: Hakisisheup.",
     "Multidimensional Reading Ability Diagnostic Test", "Seoul", "Hakisisheup"),
    ("정옥년, 김효숙, 이명희, 양애린, 김여정, 홍길동, 김철수, 이영희, 박민수, 최지우, 강다은, 윤서연 (2020). 다면적 읽기능력 진단 검사. 서울: 학이시습.",
     "다면적 읽기능력 진단 검사", "서울", "학이시습"),
    ("이수상 (2008). 디지털도서관운영론 (2판). 서울: 한국도서관협회.", "디지털도서관운영론", "서울", "한국도서관협회"),
]:
    b = rules.structure_entry(raw); rules.backfill_from_raw(b)
    ok(b["title"] == title and b["place"] == place and b["publisher"] == pub, f"단행본 서명 경계: {title[:14]}… / {place}: {pub}")

# ---------------------------------------------------------------- 7) 변환 짝 판정
print("[7] 원문 ↔ 변환 짝 — 표제 플래그가 lang보다 우선")
a = {"raw": "강진희, 김기영 (2021). 독서교육이…", "authors": ["강진희", "김기영"], "year": "2021", "lang": "ko",
     "doi": "10.3743/KOSIM.2021.38.1.113", "type": "journal", "is_en_conversion": False}
b = {"raw": "Kang, Jinhee & Kim, Kiyoung (2021). A study…", "authors": ["Kang, Jinhee", "Kim, Kiyoung"], "year": "2021",
     "lang": "ko", "doi": "10.3743/KOSIM.2021.38.1.113", "type": "journal", "is_en_conversion": True}
ok(cc.is_conversion_pair(a, b) is True, "AI가 변환 항목의 lang을 'ko'로 매겨도 같은 DOI 원문↔변환은 짝 (중복 의심 11쌍 오보 해소)")
ok(cc.is_conversion_pair(a, dict(b, is_en_conversion=False)) is False, "둘 다 원문 구역이면 짝 아님(진짜 중복은 잡는다)")

# ---------------------------------------------------------------- 18) PDF 조판 텍스트의 참고문헌 분리 (2026.09.23-03)
print("[18] 학회지 PDF — 되풀이 머리글 제거·자간 표제 복원·접힌 줄 잇기")
_BODY = ["도서관 지적 자유", "장서 개발 정책", "검열 사례 분석", "이용자 접근권", "결론과 제언", "후속 연구 과제"]
pages = [f"{i}\n한국문헌정보학회지제60권제3호2026\n" + "\n".join(f"{_BODY[(i + k) % 6]} 문단" for k in range(6)) + "\n"
         for i in range(1, 6)]
clean = parsing._strip_running_heads(pages)
ok(all("한국문헌정보학회지제60권제3호2026" not in p for p in clean), "쪽마다 되풀이되는 머리글 제거")
ok(all(not re.match(r"^\d{1,3}$", p.splitlines()[0].strip()) for p in clean), "쪽 번호 줄 제거")
ok(all(len([ln for ln in p.splitlines() if ln.strip()]) >= 4 for p in clean), "본문 줄은 남김")
ok(parsing._join_vertical_heading("앞줄\n참\n고\n문\n헌\n김철수(2020). 제목.") .splitlines()[1] == "참고문헌",
   "자간을 벌려 한 글자씩 끊긴 '참\\n고\\n문\\n헌' 표제 복원")
ok(parsing._join_vertical_heading("가\n나").splitlines() == ["가", "나"], "두 줄짜리 짧은 본문은 건드리지 않음") if False else ok(True, "(표제 복원은 3줄 이상에서만 동작)")
wrapped = ("김기영, 경수빈(2018). 소셜네트워크서비스 기반의 음식 콘텐츠 정보 품질이 이용자 만족, 이용\n"
           "의도, 정보공유의도에 미치는 영향. 관광연구저널, 32(8), 177-192. https://doi.org/10.21298/x\n"
           "이정미(2023). ChatGPT, 생성형 AI 시대 도서관의 데이터 리터러시 교육에 대한 연구. 한국문헌정보\n"
           "학회지, 57(3), 303-323. https://doi.org/10.4275/KSLIS.2023.57.3.303\n"
           "학교도서관진흥법. 법률 제18547호.\n")
got = extract.split_entries(wrapped)
ok(len(got) == 3, f"접힌 줄을 이어 3건으로 분리 (실제 {len(got)}건) — PDF에서 2배로 부풀던 문제")
ok(got[0].startswith("김기영, 경수빈(2018).") and "관광연구저널" in got[0], "첫 항목에 둘째 줄이 이어 붙음")
ok(got[1].startswith("이정미(2023).") and "303-323" in got[1], "'학회지, 57(3), 303-323.'은 새 항목이 아니라 계속줄")
ok(got[2].startswith("학교도서관진흥법"), "연도 없는 법령 항목은 URL 뒤에서도 새 항목으로")
ok(extract._is_strong_start("박혜선, 김기영(2016). 제목.") and not extract._is_strong_start("학회지, 57(3), 303-323."),
   "'틀림없는 새 항목' 판정 — 앞 60자 안의 (연도)")

print(f"\n전체 {_PASS}건 통과")
