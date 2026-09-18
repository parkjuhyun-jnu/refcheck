# -*- coding: utf-8 -*-
"""본문 내 인용 ↔ 참고문헌 목록 대조.

문편협 기준 II-1-(1): 본문에서 인용한 문헌은 반드시 참고문헌 목록에 포함해야 하며,
참고문헌 목록은 본문에서 인용·언급한 문헌만 제시한다.
"""
import re

_YEAR = r"(?:1[89]\d{2}|20\d{2})[a-z]?|n\.d\.|발행년불명|발행년\s*미상|연도\s*미상"   # (n.d.)·(발행년불명)도 인용이다

# 괄호 인용: (홍길동, 2020), (홍길동 외, 2020; 김철수, 2021), (Smith et al., 2020, 15-17)
_PAREN_RE = re.compile(r"\(([^()]{2,120}?(?:1[89]\d{2}|20\d{2}|n\.d\.|발행년불명|발행년\s*미상|연도\s*미상)[^()]{0,40})\)")

# 서술 인용: 홍길동(2020), 홍길동 외(2020), Smith(2020), Smith et al.(2020), Golder와 Huberman(2006)
# 연도 뒤는 닫는 괄호·쉼표·콜론이어야 한다 — '(2025년 기준)' 같은 본문 괄호가
# 서술 인용으로 오인되어 '이루어졌다(2025…'가 허위 누락으로 보고됐다(2026-09 실측)
# 이름 길이 14자까지 — '전라남도교육청(2025)'·'울산광역시교육연구정보원(2024)' 같은 기관명이
# 5자에서 잘려 '남도교육청(2025)'로 대조되던 문제(2026-09-18 편집위원회 검토본 실측)
_KO_NAME = r"[가-힣]{2,14}"
_CJK_NAME = r"[一-鿿々〆・ー]{2,14}"           # 全国学力・学習状況調査 — 가운뎃점(・) 포함 일문 기관명
_WEST_NAME = r"[A-Z][A-Za-z\-']{2,}(?:\s+(?:of|for|the|and|on|in|de|du|des|et)\s+[A-Z][A-Za-z\-']+|\s+[A-Z][A-Za-z\-']+){0,6}"
_NARR_KO = re.compile(r"(" + _KO_NAME + r")(\s*외)?\s*\(\s*(" + _YEAR + r")(?=\s*[\),:;])")
_NARR_CJK = re.compile(r"(" + _CJK_NAME + r")\s*\(\s*(" + _YEAR + r")(?=\s*[\),:;])")
_NARR_WEST = re.compile(
    r"(" + _WEST_NAME + r")(?:\s+(?:et al\.?|and|&)\s*[A-Z]?[A-Za-z\-']*|와|과)?\s*\(\s*(" + _YEAR + r")(?=\s*[\),:;])"
)

# 법령 인용 — 연도 없이 '○○법 제6조', '○○법[법률 제18298호]', '「○○법」'으로 적는다.
# 공통기준 Ⅱ-1)(1)은 본문에서 인용한 문헌을 모두 목록에 싣게 하므로 이런 법령도 목록에
# '법령명. 공포번호' 항목이 있어야 한다(2026-09-18 편집위원회 지적: 진로교육법[법률 제18298호]
# 제6조가 목록에 없음). 단순 언급('개정 학교도서관진흥법 시행 이후')은 인용으로 보지 않는다.
_LAW_NAME = r"[가-힣]{1,20}(?:법|시행령|시행규칙|조례|규칙)"
_LAW_CITE = re.compile(
    r"(?<![가-힣])(" + _LAW_NAME + r")\s*(?:"
    r"[\[(（]\s*(?:법률|대통령령|교육부령|총리령|조례)\s*제\s*\d+\s*호\s*[\])）]"   # 진로교육법[법률 제18298호]
    r"|제\s*\d+\s*조"                                                          # 학교도서관진흥법 제3조
    r")"
)
_LAW_QUOTE = re.compile(r"「\s*(" + _LAW_NAME + r")\s*」")                      # 「학교도서관진흥법」

_STOPWORDS_KO = {"그림", "부록", "제시", "발행", "개정", "조사", "연구", "분석", "결과", "이용", "적용", "기준",
                 "차이", "현황", "비교", "변화", "증가", "감소", "연수", "근속연수", "수준", "분포", "방법", "대상",
                 # 표의 주기·시점 칸 — '매년(2005)', '격년(2016)', '시작(2007)'
                 "매년", "격년", "반기", "분기", "시작", "시행", "실시", "도입", "기점", "이후", "이전", "현재"}
_STOPWORDS_WEST = {"Table", "Figure", "Appendix", "Chapter", "Section", "Vol", "No", "The", "In", "According"}
_LAW_STOP = {"방법", "기법", "문법", "어법", "화법", "헌법", "민법", "형법", "상법", "국제법"}  # 학문·일반어


def _norm_year(y: str) -> str:
    y = (y or "").strip()
    if re.fullmatch(r"n\.d\.|발행년불명|발행년\s*미상|연도\s*미상", y):
        return "n.d."
    return re.sub(r"[a-z]$", "", y)


def _norm_name(s: str) -> str:
    """이름 대조용 정규화 — 공백·가운뎃점·괄호 안 부기 제거, 소문자."""
    s = re.sub(r"\([^)]*\)", "", s or "")
    return re.sub(r"[\s·ㆍ・\-]+", "", s).lower()


def extract_citations(body_text: str) -> list[dict]:
    """본문에서 (이름, 연도) 인용 후보 추출. 법령 인용은 연도 '' + law=True."""
    found: dict[tuple, dict] = {}

    def add(name: str, year: str, snippet: str, law: bool = False, co: bool = False):
        name = name.strip().rstrip(",")
        if not name or name in _STOPWORDS_KO or name in _STOPWORDS_WEST:
            return
        if re.fullmatch(r"[가-힣]+다", name):
            # '시행되었다(2025)' 같은 서술어+연도 괄호 — '다'로 끝나는 국내 저자명은
            # 사실상 없다(외국인명은 기준상 원어로 적으므로 한글 표기 충돌도 없음)
            return
        key = (name, _norm_year(year))
        if key not in found:
            found[key] = {"name": name, "year": _norm_year(year), "snippet": snippet.strip()[:90]}
            if law:
                found[key]["law"] = True
            if co:
                # 공저자 자리의 이름 '(강봉숙, 박주현, 2019)'의 박주현 — 목록 대조는 첫 저자로
                # 하므로 누락 판정엔 안 쓰고, '이 이름은 어느 해로 인용됐나'에만 쓴다
                found[key]["co"] = True
        elif not co and found[key].get("co"):
            found[key].pop("co", None)

    # 괄호 인용 — 세미콜론 구분 복합 인용 처리
    for m in _PAREN_RE.finditer(body_text):
        inner = m.group(1)
        if re.search(r"https?://|표\s*\d|그림\s*\d|Figure|Table", inner):
            continue
        for seg in inner.split(";"):
            seg = seg.strip()
            years = re.findall(_YEAR, seg)
            if not years:
                continue
            ym = re.search(_YEAR, seg)
            name_part = seg[: ym.start()].strip().rstrip(",").strip()
            name_part = re.sub(r"\s*(외|et al\.?|&.*|와$|과$)\s*$", "", name_part).strip().rstrip(",")
            # 복수 저자 표기 "김영석, 이용재" → 첫 저자
            parts = [x.strip() for x in re.split(r"[,·]", name_part) if x.strip()]   # 'and'로는 안 나눈다(기관명 속 and)
            first = parts[0] if parts else ""
            if re.fullmatch(_KO_NAME + r"|" + _WEST_NAME + r"|" + _CJK_NAME, first):
                # 한 저자의 여러 해 '(全国学校図書館協議会, 2025a, 2025b)'는 해마다 인용으로 센다
                for year in years:
                    add(first, year, m.group(0))
                    for co_name in parts[1:4]:
                        if re.fullmatch(_KO_NAME + r"|" + _WEST_NAME, co_name):
                            add(co_name, year, m.group(0), co=True)

    # 서술 인용의 앞 공저자 '박주현과 변우열(2018)' — 박주현도 2018로 인용된 이름
    for m in re.finditer(r"(" + _KO_NAME + r")\s*(?:와|과|,|·)\s*(" + _KO_NAME + r")(?:\s*외)?\s*\(\s*(" + _YEAR + r")(?=\s*[\),:;])", body_text):
        if m.group(1) not in _STOPWORDS_KO and not re.fullmatch(r"[가-힣]+다", m.group(1)):
            add(m.group(1), m.group(3), m.group(0), co=True)

    # 서술 인용
    for m in _NARR_KO.finditer(body_text):
        add(m.group(1), m.group(3), m.group(0))
    for m in _NARR_CJK.finditer(body_text):
        add(m.group(1), m.group(2), m.group(0))
    for m in _NARR_WEST.finditer(body_text):
        add(m.group(1), m.group(2), m.group(0))

    # 법령 인용(연도 없음)
    for rx in (_LAW_CITE, _LAW_QUOTE):
        for m in rx.finditer(body_text):
            name = m.group(1)
            if name in _LAW_STOP or len(name) < 3:
                continue
            add(name, "", m.group(0), law=True)

    return list(found.values())


def _year4(e: dict) -> str:
    m = re.match(r"\d{4}", e.get("year") or "")
    return m.group(0) if m else ""


def is_conversion_pair(a: dict, b: dict) -> bool:
    """국문 문헌과 그 '국한문 참고문헌의 영문 표기'(영문 변환) 짝인지.

    학회 투고규정은 국문 참고문헌에 영문화 목록을 병기하게 하므로, 같은 문헌이
    국문·영문으로 한 번씩 나타나는 것은 중복이 아니라 규정 이행이다. DOI가 같다고
    '중복 의심'으로 몰거나, 영문 표기를 '본문에 인용 없음'으로 몰면 안 된다
    (국문 본문은 국문 표기로 인용한다. 2026-09 실측).
    """
    if "is_en_conversion" in a or "is_en_conversion" in b:
        # 원고가 소절 표제('국한문 참고문헌의 영문 표기' 등)로 변환 구역을 명시한 경우
        # extract 단계가 달아 준 플래그가 우선이다 — 변환끼리·원문끼리는 짝이 아니고,
        # 표제 밖의 서양어 문헌은 변환 표기가 아니므로 휴리스틱으로 짝짓지 않는다.
        if bool(a.get("is_en_conversion")) == bool(b.get("is_en_conversion")):
            return False
        # 표제로 확정된 변환 항목은 lang 표시와 무관하게 '영문 쪽'이다 — AI가 로마자 변환
        # 항목의 lang을 'ko'로 매기면 아래 lang 검사가 둘 다 국문으로 보고 짝을 부정해
        # 같은 DOI의 원문↔변환이 '중복 의심 11쌍'으로 보고됐다(2026-09-18 실측)
        ko, west = (a, b) if b.get("is_en_conversion") else (b, a)
    else:
        if (a.get("lang") == "west") == (b.get("lang") == "west"):
            return False  # 둘 다 국문이거나 둘 다 서양어면 변환 짝이 아니다
        ko, west = (a, b) if b.get("lang") == "west" else (b, a)
    da, db = (ko.get("doi") or "").lower(), (west.get("doi") or "").lower()
    if da and da == db:
        return True
    if _year4(ko) != _year4(west):
        return False
    if ko.get("volume") and ko.get("pages") and \
            ko.get("volume") == west.get("volume") and ko.get("issue") == west.get("issue") and \
            re.sub(r"\D", "", ko["pages"]) == re.sub(r"\D", "", west.get("pages") or ""):
        return True
    # 권·호·면수가 없는 유형(학위논문·보고서·법령·단행본)은 같은 해 같은 유형이면 짝으로 본다
    # — 잘못 짝지어도 결과는 경고 억제뿐이라, 허위 경고보다 해가 작다
    return ko.get("type") in ("thesis", "report", "law", "book") and ko.get("type") == west.get("type")


def en_conversion_flags(entries: list[dict]) -> list[bool]:
    """항목별 '영문 변환 표기' 여부.

    원고가 소절 표제('국한문 참고문헌의 영문 표기'·'영문 변환 목록' 등)를 명시해
    extract 단계에서 is_en_conversion 플래그가 달렸으면 그것을 그대로 쓴다(표제 기반이
    더 정확하고, DOI·권호면수가 없는 유형까지 빠짐없이 잡는다). 플래그가 없는 원고에서만
    서지 요소 휴리스틱(is_conversion_pair)으로 추정한다.
    """
    if any("is_en_conversion" in e for e in entries):
        return [bool(e.get("is_en_conversion")) for e in entries]
    ko_entries = [e for e in entries if e.get("lang") != "west"]
    return [e.get("lang") == "west" and any(is_conversion_pair(k, e) for k in ko_entries)
            for e in entries]


def _ref_keys(entry: dict) -> set[tuple[str, str]]:
    """참고문헌 한 건에서 매칭용 (이름, 연도) 키 집합 생성.

    연도는 앞 4자리만 쓴다 — 신문 기사 '백원근 (2024. 04. 25.)'의 연도가 '2024. 04. 25.'로
    남아 본문 (백원근, 2024)와 어긋나던 문제. 서양 기관 저자는 전체 이름과 마지막 낱말
    ('Great School Libraries' → 'libraries') 둘 다, 괄호 약칭('…Achievement(IEA)' → 'iea')도 키로.
    """
    keys = set()
    year = _year4(entry) or _norm_year(entry.get("year", ""))
    if not year and re.search(r"n\.d\.|발행년불명|미상", entry.get("raw") or ""):
        year = "n.d."
    years = {year}
    if entry.get("orig_year"):
        years.add(_norm_year(str(entry["orig_year"]))[:4])
    for a in entry.get("authors") or []:
        a = a.strip()
        if not a:
            continue
        abbr = re.findall(r"\(([A-Z][A-Za-z]{1,10})\)", a)
        if entry.get("lang") == "west":
            last = re.sub(r"\([^)]*\)", "", a.split(",")[0]).strip()
            names = {last.lower()}
            if " " in last:
                names.add(last.split()[-1].lower())
            names.update(x.lower() for x in abbr)
            for y in years:
                for n in names:
                    keys.add((n, y))
        else:
            name = re.sub(r"\s*(외|편|공편|옮김|번역)\s*$", "", a).strip()
            for y in years:
                keys.add((name, y))
                for x in abbr:
                    keys.add((x.lower(), y))
    if not entry.get("authors"):
        # 구조화가 저자를 못 건진 기관 저자 — 'International Association for the Evaluation of
        # Educational Achievement(IEA) (2021)'는 raw의 '(연도)' 앞이 이름이다(2026-09-18 실측)
        m = re.match(r"^\s*(.{2,140}?)\s*[\.。]?\s*\(\s*(?:" + _YEAR + r")\s*\)", entry.get("raw") or "")
        if m:
            nm = m.group(1).strip().rstrip(".")
            names = {nm, nm.lower()}
            abbr = re.findall(r"\(([A-Z][A-Za-z]{1,10})\)", nm)
            names.update(x.lower() for x in abbr)
            bare = re.sub(r"\([^)]*\)", "", nm).strip()
            # 공동 기관 저자 'CILIP, School Library Association, & School Libraries Group' —
            # 기관마다 전체 이름과 마지막 낱말을 키로(본문은 첫 기관으로 인용한다)
            for part in re.split(r",\s*|\s*&\s*|\s+and\s+", bare):
                part = part.strip()
                if len(part) >= 3:
                    names.add(part.lower())
                    if " " in part:
                        names.add(part.split()[-1].lower())
            for y in years:
                for n in names:
                    keys.add((n, y))
        else:
            title = (entry.get("title") or "")[:12]
            for y in years:
                keys.add((title, y))
    return keys


def _law_key(entry: dict) -> str:
    """법령 항목의 법령명(공백·가운뎃점 제거) — 없으면 ''."""
    raw = (entry.get("raw") or "").strip()
    if entry.get("type") == "law":
        cand = entry.get("title") or (entry.get("authors") or [""])[0] or raw
    elif re.match(r"^" + _LAW_NAME + r"\s*[\.。]?\s*(?:법률|대통령령|교육부령|총리령|조례)\s*제\s*\d+\s*호", raw):
        cand = raw
    else:
        return ""
    m = re.match(r"^\s*(" + _LAW_NAME + r")", cand)
    return _norm_name(m.group(1)) if m else ""


def cross_check(body_text: str, entries: list[dict]) -> dict:
    """본문 인용과 참고문헌 목록 대조 결과.
    {citations_found, cited_not_listed: [...], listed_not_cited: [...]}"""
    citations = extract_citations(body_text)

    all_ref_keys: set[tuple[str, str]] = set()
    per_entry_keys: list[set] = []
    law_keys: dict[str, int] = {}
    for i, e in enumerate(entries):
        ks = _ref_keys(e)
        per_entry_keys.append(ks)
        all_ref_keys |= ks
        lk = _law_key(e)
        if lk:
            law_keys.setdefault(lk, i)

    ref_names = {k[0] for k in all_ref_keys}
    cited_not_listed = []
    matched_keys: set[tuple[str, str]] = set()
    matched_entries: set[int] = set()
    # 본문에 인용된 이름별 연도 — '교육부(2024)'만 인용된 원고에서 목록의 '교육부 (2025)'를
    # 이름이 보인다는 이유로 인용된 것으로 넘기지 않기 위해(2026-09-18 편집위원회 지적)
    cited_years: dict[str, set[str]] = {}
    for c in citations:
        name = c["name"]
        if c.get("co"):
            cited_years.setdefault(name.lower(), set()).add(c["year"])
            continue
        if c.get("law"):
            lk = _norm_name(name)
            hit_i = law_keys.get(lk)
            if hit_i is None:
                # 약칭('학교도서관법')이 정식 명칭('학교도서관진흥법')에 포함되는 경우도 인정
                hit_i = next((i for k, i in law_keys.items() if lk and (lk in k or k in lk)), None)
            if hit_i is None:
                cited_not_listed.append({**c, "name_only_match": False,
                                         "note": "법령 — 목록에 '법령명. 공포번호' 항목이 필요"})
            else:
                matched_entries.add(hit_i)
            continue
        cited_years.setdefault(name.lower(), set()).add(c["year"])
        key_candidates = [(name, c["year"]), (name.lower(), c["year"])]
        if " " in name:   # 'Great School Libraries' → 마지막 낱말로도
            key_candidates.append((name.split()[-1].lower(), c["year"]))
        hit = None
        for kc in key_candidates:
            if kc in all_ref_keys:
                hit = kc
                break
        if hit is None and re.fullmatch(r"[가-힣]{4,}", name):
            # 기관명 일부로 적은 인용 — 표의 '광주광역시(2022)' ↔ 목록 '광주광역시교육청 (2022)'.
            # 같은 해 목록 이름이 인용 이름으로 시작하면(또는 그 반대) 같은 문헌으로 본다
            hit = next((k for k in all_ref_keys
                        if k[1] == c["year"] and len(k[0]) >= 4
                        and (k[0].startswith(name) or name.startswith(k[0]))), None)
        if hit:
            matched_keys.add(hit)
        else:
            # 이름만 일치(연도 상이)도 목록 누락으로 보지 않되 메모
            name_only = name in ref_names or name.lower() in ref_names
            cited_not_listed.append({**c, "name_only_match": name_only})

    listed_not_cited = []
    conv_flags = en_conversion_flags(entries)
    # 이름 폴백은 인용 구간을 뺀 본문으로 — '(강봉숙, 박주현, 2019)' 안의 박주현이
    # '박주현 외 (2026)'의 인용 근거가 되지 않게
    body_wo = _PAREN_RE.sub(" ", body_text)
    for rx in (_NARR_KO, _NARR_CJK, _NARR_WEST):
        body_wo = rx.sub(" ", body_wo)
    body_lower = body_wo.lower()
    for i, (e, ks, is_conv) in enumerate(zip(entries, per_entry_keys, conv_flags)):
        if i in matched_entries:
            continue
        if not ks:
            continue
        if is_conv:
            # '국한문 참고문헌의 영문 표기' 항목 — 본문(국문)은 국문 표기로 인용하므로
            # 변환 표기가 본문에 없는 것이 정상이다(2026-09 실측)
            continue
        if _law_key(e):
            # 법령 항목은 본문의 '○○법 제N조'·'[법률 제N호]'·「○○법」 인용으로만 짝지어졌다.
            # 그래도 못 찾았으면 법령명이 본문에 한 번이라도 나오면 인용으로 본다(단순 언급 허용)
            lk = _law_key(e)
            if lk and lk in _norm_name(body_text):
                continue
            # 본문에 법령명이 아예 없으면 아래 일반 규칙으로 '인용 없음' 보고
        if not (ks & matched_keys):
            names = {k[0] for k in ks}
            # 같은 이름이 본문에 다른 연도로 인용돼 있으면 '이름이 보인다'로 넘기지 않는다 —
            # 그 인용은 다른 항목의 것이다. 이 항목의 인용은 없다고 보고 연도를 함께 알린다.
            other_years = set()
            for n in names:
                other_years |= cited_years.get(n.lower(), set())
            other_years.discard(_year4(e))
            if other_years:
                listed_not_cited.append({
                    "raw": e.get("raw", "")[:120],
                    "authors": ", ".join(e.get("authors") or [])[:60],
                    "year": e.get("year", ""),
                    "note": "본문에는 같은 저자의 " + ", ".join(sorted(y for y in other_years if y))
                            + "년 문헌만 인용됨 — 이 항목의 인용 표기 확인",
                })
                continue
            # 인용 표기가 아예 없어도 이름이 본문에 등장하면 인용된 것으로 간주(연도 표기 차이 허용)
            if any((n and len(n) >= 2 and (n in body_wo or n in body_lower)) for n in names):
                continue
            listed_not_cited.append({
                "raw": e.get("raw", "")[:120],
                "authors": ", ".join(e.get("authors") or [])[:60],
                "year": e.get("year", ""),
            })

    # 같은 해에 '부산광역시(2016)'와 '부산광역시교육청(2016)'가 함께 잡히면(표의 지역 칸 + 본문)
    # 짧은 쪽은 긴 쪽의 일부다 — 한 건으로 보고한다
    longer = [(c["name"], c["year"]) for c in cited_not_listed]
    cited_not_listed = [c for c in cited_not_listed
                        if not any(n != c["name"] and y == c["year"] and n.startswith(c["name"])
                                   and len(c["name"]) >= 4 for n, y in longer)]

    # 본문 인용은 있는데 목록에 없는 문헌 ↔ 목록엔 있는데 인용이 없는 같은 해 문헌:
    # 이름 표기가 서로 달라 짝이 안 맞은 것일 수 있다 — '(全国学力・学習状況調査, 2026)' ↔
    # '国立教育政策研究所 (2026)'(2026-09-18 편집위원회 지적 '저자명 불일치'). 후보로 부기한다.
    for c in cited_not_listed:
        if c.get("law") or not c.get("year"):
            continue
        cands = [x for x in listed_not_cited if _norm_year(str(x.get("year", "")))[:4] == c["year"]]
        if cands:
            # 인용 이름과 같은 문자 계열(한자·가나 / 한글 / 로마자) 항목을 앞에 — 표기 차이일 확률이 높다
            def _script(t: str) -> str:
                return ("cjk" if re.search(r"[一-鿿぀-ヿ]", t) else
                        "ko" if re.search(r"[가-힣]", t) else "latin")
            sc = _script(c["name"])
            cands.sort(key=lambda x: 0 if _script(x["raw"]) == sc else 1)
            c["candidates"] = [x["raw"][:60] for x in cands[:3]]

    return {
        "citations_found": len(citations),
        "cited_not_listed": cited_not_listed,
        "listed_not_cited": listed_not_cited,
    }


# ---------------------------------------------------------------- 국문 원문 ↔ 영문 변환 이름 대조
#
# '국한문 참고문헌의 영문 표기'에 빠진 국문 문헌을 찾으려면 원문과 변환 항목을 짝지어야
# 하는데, DOI·권호면수가 없는 기관 발간물·웹자료·보고서는 지금까지 '유형+연도가 유일'할
# 때만 짝이 됐다. 같은 해 보고서가 여럿이면(교육부 2024·경기도교육청 2024·울산…2024) 전부
# 짝을 못 찾아 누락 판정을 할 수 없었다. 이름으로 한 번 더 짝짓는다 — 사람은 성씨 로마자,
# 기관은 지역·기관 유형 낱말의 영역(2026-09-18 편집위원회 지적: 변환 목록 누락 6건).

_SURNAME_ROMAN = {
    "김": "kim gim", "이": "lee yi rhee ri", "박": "park pak bak", "최": "choi choe", "정": "jung jeong chung cheong",
    "강": "kang gang", "조": "cho jo", "윤": "yoon yun youn", "장": "jang chang", "임": "lim im rim yim",
    "한": "han", "오": "oh o", "서": "seo suh", "신": "shin sin", "권": "kwon gwon kweon", "황": "hwang whang",
    "안": "ahn an", "송": "song", "유": "yoo yu you ryu", "류": "ryu ryoo lyu yu", "홍": "hong",
    "전": "jeon chun jun chon", "고": "ko koh go", "문": "moon mun", "양": "yang", "손": "son sohn",
    "배": "bae pae", "백": "baek paik baik back", "허": "heo hur huh her", "노": "noh roh no ro", "남": "nam",
    "심": "sim shim", "하": "ha", "주": "joo ju chu", "구": "koo gu ku goo", "민": "min", "변": "byun byeon byon pyun",
    "곽": "kwak gwak kwag", "성": "sung seong", "차": "cha", "우": "woo wu", "길": "kil gil", "현": "hyun hyeon",
    "나": "na ra nah la", "도": "do doh", "석": "seok suk sok", "표": "pyo", "채": "chae", "원": "won", "진": "jin chin",
    "옥": "ok", "맹": "maeng", "방": "bang pang", "편": "pyeon pyon pyun", "명": "myung myeong", "천": "cheon chun",
    "공": "kong gong", "함": "ham", "염": "yeom yum youm", "여": "yeo yuh", "소": "so soh", "선": "sun seon",
    "설": "seol sul", "마": "ma mah", "연": "yeon", "위": "wi wee", "기": "ki gi kee", "반": "ban", "왕": "wang",
    "금": "keum geum kum", "육": "yook yuk", "인": "in ihn", "제": "je jae", "모": "mo moh", "탁": "tak",
    "국": "kook guk kuk", "엄": "eom um uhm om", "어": "eo uh", "은": "eun un", "용": "yong", "예": "ye", "봉": "bong",
    "사": "sa", "부": "bu boo", "피": "pi", "감": "gam kam", "태": "tae", "갈": "gal kal", "경": "kyung gyeong kyoung",
    "빈": "bin", "종": "jong", "승": "seung", "시": "si shi", "목": "mok", "지": "ji jee chi", "화": "hwa", "상": "sang",
    "남궁": "namgung namkoong", "황보": "hwangbo", "선우": "sunwoo seonu", "제갈": "jegal", "사공": "sagong",
    "서문": "seomun", "독고": "dokgo", "동방": "dongbang",
}

# 기관명 낱말 → 영문에서 찾을 대체어('|' 구분, 구는 통째로 찾는다). 하나라도 있으면 그 낱말은 맞은 것
_ORG_WORDS = [
    ("서울", "seoul"), ("부산", "busan|pusan"), ("대구", "daegu|taegu"), ("인천", "incheon|inchon"),
    ("광주", "gwangju|kwangju"), ("대전", "daejeon|taejon"), ("울산", "ulsan"), ("세종", "sejong"),
    ("경기", "gyeonggi|kyonggi|kyunggi"), ("강원", "gangwon|kangwon"), ("충청북도", "chungcheongbuk|chungbuk"),
    ("충북", "chungcheongbuk|chungbuk"), ("충청남도", "chungcheongnam|chungnam"), ("충남", "chungcheongnam|chungnam"),
    ("전라북도", "jeollabuk|jeonbuk"), ("전북", "jeollabuk|jeonbuk"), ("전라남도", "jeollanam|jeonnam"),
    ("전남", "jeollanam|jeonnam"), ("경상북도", "gyeongsangbuk|gyeongbuk"), ("경북", "gyeongsangbuk|gyeongbuk"),
    ("경상남도", "gyeongsangnam|gyeongnam"), ("경남", "gyeongsangnam|gyeongnam"), ("제주", "jeju"),
    ("교육부", "ministry of education|moe"), ("문화체육관광부", "ministry of culture|mcst"),
    ("행정안전부", "ministry of the interior|ministry of interior"),
    ("교육연구정보원", "education research|research and information|research & information"),
    ("교육청", "office of education|education office|board of education"),
    ("학술정보원", "research information|keris|education and research information"),
    ("교육과정평가원", "curriculum|evaluation|kice"),
    ("국립중앙도서관", "national library of korea"), ("국회도서관", "national assembly library"),
    ("도서관협회", "library association"), ("도서관", "library|libraries"), ("협회", "association|council"),
    ("학회", "society|association"), ("연구원", "institute|research"), ("연구소", "institute|research"),
    ("진흥원", "promotion|agency|institute|foundation"), ("센터", "center|centre"), ("대학교", "university"),
    ("위원회", "committee|commission|council"), ("재단", "foundation"), ("공단", "corporation|service"),
    ("한국", "korea|korean"), ("국가", "national|korea"), ("국립", "national"),
]
_ORG_SUFFIX = re.compile(r"(교육청|부|원|회|청|센터|협회|재단|연구소|도서관|대학교|학교|공사|공단|위원회|진흥원|평가원|정보원|학회)$")
_PERSON_RE = re.compile(r"^[가-힣]{2,4}$")


def _romanized_surname(west_author: str) -> str:
    a = (west_author or "").strip()
    if not a:
        return ""
    last = a.split(",")[0].strip() if "," in a else a.split()[-1]
    return re.sub(r"[^a-z]", "", last.lower())


def _is_org(ko: str) -> bool:
    return len(ko) >= 5 or bool(_ORG_SUFFIX.search(ko)) or any(w in ko for w, _ in _ORG_WORDS)


def names_compatible(ko_author: str, west_author: str) -> bool:
    """국문 저자(첫 저자)와 영문 변환의 첫 저자가 같은 사람·기관으로 보이는가.

    사람은 성씨 로마자 이형까지 허용(박 → Park/Pak/Bak), 기관은 지역·기관 유형 낱말이
    영문에 모두 있어야 한다('경기도교육청' ↔ 'Gyeonggi Provincial Office of Education').
    모르는 성씨·낱말이면 False — 이 함수는 짝을 늘리는 보조 단서일 뿐이다.
    """
    ko = re.sub(r"\s*(외|등)\s*$", "", (ko_author or "").strip())
    west = re.sub(r"\s+", " ", (west_author or "").lower())
    if not ko or not west:
        return False
    if _PERSON_RE.match(ko) and not _is_org(ko):
        sur = ko[:2] if ko[:2] in _SURNAME_ROMAN and len(ko) >= 3 else ko[:1]
        romans = set((_SURNAME_ROMAN.get(sur) or "").split())
        return bool(romans) and _romanized_surname(west_author) in romans
    hits = 0
    rest = ko
    for word, alts in _ORG_WORDS:
        if word in rest:
            rest = rest.replace(word, " ")
            if not any(alt in west for alt in alts.split("|")):
                return False
            hits += 1
    return hits >= 1


def pair_by_name(ko_entries: list[tuple[int, dict]], conv_entries: list[tuple[int, dict]]) -> dict[int, int]:
    """(인덱스, 항목) 목록끼리 같은 해 + 이름 호환 + 양쪽 유일일 때만 짝짓는다."""
    pairs: dict[int, int] = {}
    cands: dict[int, list[int]] = {}
    for i, a in ko_entries:
        ya = _year4(a) or _norm_year(a.get("year", ""))
        au = (a.get("authors") or [""])[0]
        for j, b in conv_entries:
            yb = _year4(b) or _norm_year(b.get("year", ""))
            if ya != yb or not ya:
                continue
            if names_compatible(au, (b.get("authors") or [""])[0]):
                cands.setdefault(i, []).append(j)
    used: set[int] = set()
    for i, js in cands.items():
        js = [j for j in js if j not in used]
        if len(js) == 1 and sum(1 for k, v in cands.items() if js[0] in v and k != i) == 0:
            pairs[i] = js[0]
            used.add(js[0])
    return pairs
