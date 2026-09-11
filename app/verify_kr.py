# -*- coding: utf-8 -*-
"""국내 문헌 검증 클라이언트 (실험적).

- KCI OpenAPI: 국내 학술지 논문 실존·서지 대조, 학술지 등재 여부
  (키 발급: https://www.kci.go.kr → Open API 신청, .env의 KCI_API_KEY)
- 국립중앙도서관 서지정보(SEOJI) API: 국문 단행본 ISBN·서지 대조
  (키 발급: https://www.nl.go.kr/seoji → 인증키 신청, .env의 NLK_CERT_KEY)
- 국회도서관 국가학술정보 API(공공데이터포털): 학위논문 등
  (키 발급: https://www.data.go.kr → '국회도서관 검색' 활용신청, .env의 NANET_API_KEY)
- RISS 검색 Open API(KERIS 학술연구정보서비스): 국내 학술지 논문 교차 확인·KCI 미등재지,
  국내·해외 학위논문, 단행본·연구보고서 폴백, 학술지 등재정보(KCI등재/후보)·ISSN
  (키 발급: https://www.riss.kr/apicenter → RISS 검색 API 이용 신청, .env의 RISS_API_KEY)

반환 규약: None = '질의에 맞는 자료가 없음', LookupUnavailable 예외 = '확인하지 못함'
(키·네트워크·서비스 오류). 둘을 섞으면 실존 문헌이 '미발견'으로 표시되므로 반드시 구분한다.
"""
import difflib
import re
import threading
import xml.etree.ElementTree as ET
from urllib.parse import unquote

import httpx

import http_util
from aiengine import env_get
from http_util import LookupUnavailable

_TIMEOUT = 12
_CACHE_LOCK = threading.Lock()


def _log(msg: str) -> None:
    """서버 콘솔 로그 — 콘솔 인코딩이 cp949인 Windows에서 '—' 같은 글자로 print가 죽으면
    그 예외가 검증 스레드로 번져 항목이 '검증 중 오류'가 된다(2026-09 실측). 로그는
    부가 기능이므로 인코딩 오류를 삼킨다."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode())


def _get(client: httpx.Client, url: str, params: dict) -> httpx.Response | None:
    """국내 DB GET — 재시도 포함.

    반환 None은 '질의에 맞는 자료가 없음'(4xx 등), LookupUnavailable 예외는
    '확인하지 못함'(네트워크·429·5xx)이다. 이 둘을 섞으면 실제로 존재하는
    국내 논문이 '미발견'으로 표시되므로 반드시 구분한다.
    """
    r = http_util.get_with_retry(client, url, params=params, timeout=_TIMEOUT)
    return r if r.status_code == 200 else None


def _service_key(name: str) -> str:
    """공공데이터포털 키는 인코딩 키(% 포함)일 수 있음 — httpx가 재인코딩하므로 디코딩해 전달."""
    key = env_get(name)
    if "%" in key:
        return unquote(key)
    return key


def kr_api_status() -> dict:
    return {
        "kci": bool(env_get("KCI_API_KEY")),
        "nlk": bool(env_get("NLK_CERT_KEY")),
        "nanet": bool(env_get("NANET_API_KEY")),
        # 인증 오류가 확정된 뒤에는 False — 판정 문구가 'RISS까지 대조했다'고 주장하지 않게
        "riss": riss_enabled(),
        # 국가법령정보센터는 키 없이도 조회된다(활용 안내서의 예시 OC) — 자기 OC를 두면 그것을 쓴다
        "law": True, "law_oc": law_oc(),
    }


_HANGUL_RE = re.compile(r"[가-힣]")


def _norm(s: str) -> str:
    return re.sub(r"[\s::\-·,\.\?!「」『』\"'()\[\]]+", "", (s or "").lower())


def _sim(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _norm_en_author(s: str) -> str:
    """KCI 등록 영문 저자명 정돈 — '성, 이름' 꼴로 통일하되 철자는 바꾸지 않는다.

    등록 데이터에는 이형이 섞여 온다(2026-09 실측): 전각 콤마 'Byun，Woo-Yeol',
    소문자 'kwon, jae-hyun', 이름-성 순서 'Younghee Noh', 붙은 콤마 'Kwon,Eun-Kyung'.
    표기 자체(발행본에 인쇄된 철자)가 전거이므로 순서·구두점·첫 글자만 정돈한다.
    """
    s = re.sub(r"\s+", " ", (s or "").replace("，", ",")).strip().strip(",").strip()
    if not s:
        return s

    def cap(w: str) -> str:
        return w[:1].upper() + w[1:] if w else w

    if "," in s:
        last, first = [p.strip() for p in s.split(",", 1)]
    else:
        parts = s.split(" ")
        if len(parts) == 1:
            return cap(s)
        # 콤마 없는 등록은 성 위치가 제각각이다(실측: 'Park Juhyeon' 성 앞 /
        # 'Younghee Noh' 성 뒤) — 로마자 한국 성씨 목록으로 성을 찾는다
        from formatter import _KR_SURNAMES
        if parts[0].lower() in _KR_SURNAMES and parts[-1].lower() not in _KR_SURNAMES:
            last, first = parts[0], " ".join(parts[1:])
        else:
            last, first = parts[-1], " ".join(parts[:-1])
    return f"{cap(last)}, {cap(first)}" if first else cap(last)


def _main_title(s: str) -> str:
    """부제 분리 — '제목- 부제 -'·'제목: 부제'의 앞부분.

    원고는 부제를 생략하는 관행이 흔한데(실측: '…효과에 대한 분석'만 쓰고
    '- 2003-05년도 사업 결과를 중심으로 -' 생략), 정식 제목과 통으로 비교하면
    유사도가 0.80 아래로 떨어져 실존 논문이 미확인이 된다. '2003-05'처럼 단어
    안의 붙임표는 부제 경계로 보지 않는다.
    """
    return re.split(r"\s*[–—]\s*|\s+-\s*|-\s+|\s*[::]\s+", (s or ""), 1)[0].strip()


def _q_trim(s: str, limit: int = 80) -> str:
    """검색어를 단어 경계에서 자른다.

    KCI title 검색은 단어 단위 매칭이라, 글자 수로 기계적으로 자르면 마지막 단어가
    동강 나 실존 논문도 No Data가 된다(2026-09 실측: 82자 영문 제목이 80자에서
    'USA'→'U'로 잘려 0건, 온전한 단어로 자르면 1건 적중).
    """
    s = re.sub(r"\s+", " ", (s or "")).strip()
    if len(s) <= limit:
        return s
    cut = s[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.strip()


def _bare_doi(s: str) -> str:
    """'http://dx.doi.org/10.x/y' → '10.x/y'.

    KCI는 DOI를 URL 형태로 준다. Crossref 조회는 DOI를 URL 경로에 그대로 넣으므로,
    URL째 넘기면 404가 되어 철회 여부 보강이 조용히 건너뛰어진다.
    """
    return re.sub(r"^\s*(https?://)?(dx\.)?doi\.org/", "", (s or "").strip(), flags=re.I)


def _xml_root(text: str):
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


# ---------------------------------------------------------------- KCI

def _kci_error_msgs(root) -> list[str]:
    """KCI 응답에서 '조회 자체가 안 된' 사유만 추린다.

    KCI는 오류도 HTTP 200 + <resultMsg>로 준다. '등록되지 않은 서비스'(신청하지 않은
    apiCode), '등록되지 않은 key 입니다.'(키 폐기·IP 불일치), '필수 요청 파라미터가 없음'
    등이 여기 해당하며, 이를 결과 없음으로 읽으면 실존하는 논문이 '미발견'이 된다.

    반대로 검색 결과가 0건일 때도 <resultMsg>No Data</resultMsg>가 온다. 이것은 정상
    응답이므로 오류로 보면 진짜 미발견까지 '확인 못 함'으로 묻힌다 — 반드시 제외한다.
    """
    return [t for el in root.iter("resultMsg") if (t := (el.text or "").strip())
            and t.lower() != "no data"]


def kci_article_search(client: httpx.Client, title: str, author: str = "") -> dict | None:
    """KCI 논문 검색 → 최고 유사도 매칭. 반환: {sim, meta} 또는 None."""
    key = env_get("KCI_API_KEY")
    if not key or not title or len(title) < 4:
        return None
    params = {"apiCode": "articleSearch", "key": key,
              "title": _q_trim(title), "displayCount": 10}
    if author:
        params["author"] = author[:40]  # API가 지원하는 검색조건 — 동명 제목의 오매칭을 줄인다
    r = _get(client, "https://open.kci.go.kr/po/openapi/openApiSearch.kci", params)
    if r is None:
        return None
    root = _xml_root(r.text)
    if root is None:
        return None
    if (errs := _kci_error_msgs(root)):
        # 키·서비스 문제를 '미발견'으로 보고하면 실존 논문이 허위로 표시된다
        raise LookupUnavailable(f"KCI articleSearch: {'; '.join(errs)}")

    best, best_sim = None, 0.0
    for rec in root.iter("record"):
        def g(*paths):
            for p in paths:
                el = rec.find(p)
                if el is not None and (el.text or "").strip():
                    return el.text.strip()
            return ""
        # 첫 <article-title>은 lang="original"(국문). 영문 제목은 lang="english"로 따로 온다.
        art_title = g(".//article-title", ".//articleTitle", ".//title")
        title_en = next((t.text.strip() for t in rec.iter("article-title")
                         if t.get("lang") == "english" and (t.text or "").strip()), "")
        # 국내 논문을 영문 서지로 인용한 원고가 많다 — 국문·영문 제목 중 높은 쪽으로
        # 판정한다. 국문 제목과만 비교하면 KCI가 정답을 돌려줘도 유사도 0%로 버려져
        # 실존 논문이 '미확인'이 된다(2026-09 실측: 곽철완 2006 영문 인용).
        # 부제 생략 관행도 흡수한다 — 정식 제목의 부제까지 통으로 비교하면 부제만
        # 뺀 올바른 인용이 0.80 문턱 아래로 떨어진다(2026-09 실측: 곽철완 국문 인용).
        sim = max(_sim(title, art_title), _sim(title, title_en),
                  _sim(_main_title(title), _main_title(art_title)),
                  _sim(_main_title(title), _main_title(title_en)))
        if sim > best_sim:
            authors = [a.text.strip() for a in rec.iter("author") if a.text and a.text.strip()]
            # 저자가 KCI에 등록한 공식 영문 표기. 영문화 목록을 지어내지 않고 이것을 쓴다.
            authors_en = [_norm_en_author(a.get("english", "")) for a in rec.iter("author")
                          if a.get("english", "").strip()]
            best_sim = sim
            best = {
                "title": art_title,
                "authors": authors,
                "title_en": title_en,
                "authors_en": authors_en,
                "container": g(".//journal-name", ".//journalName"),
                "year": re.sub(r"\D", "", g(".//pub-year", ".//pubYear", ".//issue-date"))[:4],
                "volume": g(".//volume"),
                "issue": g(".//issue"),
                "pages": "-".join(x for x in (g(".//fpage"), g(".//lpage")) if x),
                "doi": _bare_doi(g(".//doi")),
                "uci": g(".//uci", ".//UCI"),
                "source": "KCI",
                # articleDetail 조회용 Control Number(<articleInfo article-id="ART…">)
                "kci_id": (ai.get("article-id") or "") if (ai := rec.find(".//articleInfo")) is not None else "",
            }
    if best and best_sim >= 0.80:
        best["sim"] = best_sim
        return best
    return None


_KCI_DETAIL_CACHE: dict[str, dict] = {}


def kci_article_detail(client: httpx.Client, article_id: str) -> dict | None:
    """articleDetail — Control Number(ART…)로 상세 서지 조회.

    검색 결과에는 없는 <kci-registration>(학술지 등재 구분)·ISSN·페이지를 준다.
    학술지 등재 여부는 journalSearch로도 얻을 수 있으나 그쪽은 별도 신청 항목이므로,
    이미 승인된 articleDetail로 대신한다.
    """
    key = env_get("KCI_API_KEY")
    if not key or not article_id:
        return None
    with _CACHE_LOCK:
        if article_id in _KCI_DETAIL_CACHE:
            return _KCI_DETAIL_CACHE[article_id]
    r = _get(client, "https://open.kci.go.kr/po/openapi/openApiSearch.kci",
             {"apiCode": "articleDetail", "key": key, "id": article_id})
    if r is None:
        return None
    root = _xml_root(r.text)
    if root is None:
        return None
    if (errs := _kci_error_msgs(root)):
        raise LookupUnavailable(f"KCI articleDetail: {'; '.join(errs)}")

    def g(*paths):
        for p in paths:
            el = root.find(p)
            if el is not None and (el.text or "").strip():
                return el.text.strip()
        return ""

    out = {
        "kci_registration": g(".//kci-registration"),
        "issn": g(".//issn"),
        "container": g(".//journal-name"),
        "doi": _bare_doi(g(".//doi")),
        "pages": "-".join(x for x in (g(".//fpage"), g(".//lpage")) if x),
    }
    if not any(out.values()):
        return None
    with _CACHE_LOCK:
        _KCI_DETAIL_CACHE[article_id] = out
    return out


# KCI 참고문헌 레코드의 자료유형 코드(2026-08 실측) → 이 앱의 유형 코드
_KCI_REF_TYPE = {
    "01": "journal",   # 학술지(정기간행물)
    "02": "conference",  # 학술대회논문
    "03": "book",      # 단행본
    "04": "report",    # 보고서 (2025년 4개 학회지 실측: 참고문헌의 5~9%)
    "05": "thesis",    # 학위논문
    "06": "web",       # 인터넷자원
    # "07" 기타자료는 법령·표준 등 혼합 유형이라 코드 하나로 못 박지 않는다 —
    # 아래에서 제목 패턴으로 법령만 골라내고 나머지는 unknown 유지.
}


def kci_article_references(client: httpx.Client, article_id: str) -> list[dict] | None:
    """articleDetail의 <referenceInfo> — 그 논문이 실제로 인용한 참고문헌 목록.

    referenceSearch와 혼동하면 안 된다. referenceSearch는 '이 논문이 남의 논문에
    인용된 형태'를 주므로(2026-08 실측: 같은 논문이 표기만 달리해 여러 건) 발행본의
    참고문헌으로 쓸 수 없다. 논문 자신의 참고문헌은 여기에만 있다.

    한계: 레코드에 **제1저자만** 담긴다(4인 공저도 한 명만). 저자 대조에 쓰면
    모든 항목이 '저자 누락'으로 잡히므로 호출하는 쪽에서 저자를 빼고 비교해야 한다.

    반환: 서지요소 dict 목록(형식 변환은 formatter가 한다).
    """
    key = env_get("KCI_API_KEY")
    if not key or not article_id:
        return None
    r = _get(client, "https://open.kci.go.kr/po/openapi/openApiSearch.kci",
             {"apiCode": "articleDetail", "key": key, "id": article_id})
    if r is None:
        return None
    root = _xml_root(r.text)
    if root is None:
        return None
    if (errs := _kci_error_msgs(root)):
        raise LookupUnavailable(f"KCI articleDetail: {'; '.join(errs)}")

    out: list[dict] = []
    for ref in root.iter("reference"):
        f = {ch.tag: (ch.text or "").strip() for ch in ref}
        title = f.get("title", "")
        if not title:
            continue
        etype = _KCI_REF_TYPE.get(ref.get("type-code", ""), "unknown")
        if etype == "unknown" and re.search(r"법률\s*제\s*\d+호|대통령령\s*제\s*\d+호|시행령|시행규칙|조례", title):
            # '07 기타자료'의 상당수가 법령(2025년 실측: 비블리아 108건 중 40건) —
            # '도서관법. 법률 제19592호' 식 제목만 법령으로 승격
            etype = "law"
        author = f.get("author", "")
        entry = {
            "type": etype,
            # 한글이 있으면 국내문헌 — 배열·형식이 갈린다
            "lang": "ko" if re.search(r"[가-힣]", title + author) else "west",
            "title": title,
            "authors": [author] if author else [],
            "year": re.sub(r"\D", "", f.get("pubi-year", "")
                           or f.get("registration-day", ""))[:4],
            # KCI 스키마의 오타를 그대로 따른다(isseue·pubilisher)
            "container": f.get("journal-name") or f.get("conference-name") or f.get("site-name", ""),
            "volume": f.get("volume", ""),
            "issue": f.get("isseue", ""),
            "pages": f.get("page", ""),
            "doi": _bare_doi(f.get("doi", "")),
            "url": f.get("url", ""),
            "publisher": f.get("pubilisher", ""),
            "degree": f.get("degree", ""),
            "institution": f.get("university", ""),
        }
        out.append(entry)
    return out


def kci_reference_search(client: httpx.Client, title: str, author: str = "",
                         year: str = "") -> list[str] | None:
    """KCI referenceSearch — 해당 논문에 실린 참고문헌 목록을 문자열로 반환.

    발행본 PDF 없이도 KCI에 등재된 논문의 참고문헌을 가져와 3단 비교에 쓸 수 있다.
    응답 XML의 태그 구성이 문서에 명시돼 있지 않아, 참고문헌 성격의 요소를 폭넓게 수집한다.
    """
    key = env_get("KCI_API_KEY")
    if not key or not title or len(title) < 4:
        return None
    params = {"apiCode": "referenceSearch", "key": key, "title": _q_trim(title)}
    if author:
        params["author"] = author[:40]
    if year and re.fullmatch(r"\d{4}", str(year)):
        params["pubiYr"] = str(year)
    r = _get(client, "https://open.kci.go.kr/po/openapi/openApiSearch.kci", params)
    if r is None:
        return None
    root = _xml_root(r.text)
    if root is None:
        return None
    if (errs := _kci_error_msgs(root)):
        raise LookupUnavailable(f"KCI referenceSearch: {'; '.join(errs)}")

    refs: list[str] = []
    for el in root.iter():
        tag = el.tag.lower()
        # 참고문헌 원문이 한 요소에 통째로 담긴 경우. 실제 응답은
        # <record article-id="...">저자(연도). 제목. 학술지…</record>처럼 요소의 자체
        # 텍스트에 원문이 들어오므로, record를 빼면 한 건도 얻지 못한다
        if tag.endswith(("reference", "reference-text", "referencetext",
                         "citation", "org-reference", "record")) and (el.text or "").strip():
            refs.append(re.sub(r"\s+", " ", el.text).strip())
    if not refs:
        # 서지요소가 나뉘어 온 경우 — 하위 필드를 조합해 한 건씩 만든다
        for rec in root.iter():
            if not rec.tag.lower().endswith(("reference", "record")):
                continue
            parts = [re.sub(r"\s+", " ", (c.text or "").strip())
                     for c in rec if (c.text or "").strip()]
            line = " ".join(parts).strip()
            if len(line) >= 15:
                refs.append(line)
    # 중복 제거(순서 유지)
    seen, out = set(), []
    for x in refs:
        if len(x) >= 15 and x not in seen:
            seen.add(x)
            out.append(x)
    return out or None


_KCI_JOURNAL_CACHE: dict[str, str] = {}
# journalSearch는 인증키에 승인된 apiCode가 아니면 매번 '등록되지 않은 서비스'로
# 돌아온다. 실패('')는 캐시하지 않으므로 그대로 두면 등재구분 없는 국내 문헌마다
# KCI 쿼터만 소모하는 헛호출이 반복된다 — 미승인이 확인되면 프로세스가 사는 동안
# 호출을 멈춘다. (나중에 KCI에 journalSearch를 추가 신청·승인받으면 재시작만 하면 됨)
_JOURNAL_SEARCH_OFF = False


def kci_journal_status(client: httpx.Client, journal_name: str) -> str:
    """학술지의 KCI 조회 결과: 'listed'(검색됨) / 'unlisted'(미검색) / ''(조회 불가)."""
    global _JOURNAL_SEARCH_OFF
    key = env_get("KCI_API_KEY")
    if not key or not journal_name or _JOURNAL_SEARCH_OFF:
        return ""
    jn = journal_name.strip()
    with _CACHE_LOCK:
        if jn in _KCI_JOURNAL_CACHE:
            return _KCI_JOURNAL_CACHE[jn]
    result = ""
    try:
        # 학술지 신뢰도는 부가 정보이므로, 조회 실패는 예외로 올리지 않고 ''(조회 불가)로 둔다
        # 검색어 파라미터는 title — journalName은 API가 무시한다(inputData에 되돌아오지 않음)
        r = _get(client, "https://open.kci.go.kr/po/openapi/openApiSearch.kci",
                 {"apiCode": "journalSearch", "key": key, "title": jn[:60]})
        if r is not None:
            root = _xml_root(r.text)
            if root is not None:
                errs = _kci_error_msgs(root)
                if any("등록되지 않은 서비스" in e for e in errs):
                    _JOURNAL_SEARCH_OFF = True
                    _log("[KCI] journalSearch 미승인 apiCode — 이후 호출 생략(등재 확인은 articleDetail 값만 사용)")
                elif not errs:
                    names = [el.text.strip() for el in root.iter() if el.tag.lower().endswith("journalname")
                             and el.text and el.text.strip()]
                    if not names:
                        names = [el.text.strip() for el in root.iter("journal-name") if el.text]
                    result = "listed" if any(_sim(jn, n) >= 0.85 for n in names) else "unlisted"
    except LookupUnavailable:
        result = ""
    if result:  # 조회 실패('')는 캐시하지 않음 — 다음 기회에 재시도
        with _CACHE_LOCK:
            _KCI_JOURNAL_CACHE[jn] = result
    return result


# ---------------------------------------------------------------- 국립중앙도서관(단행본)

_NLK_URL = "https://www.nl.go.kr/seoji/SearchApi.do"


def _nlk_docs(client: httpx.Client, params: dict) -> list[dict] | None:
    """SEOJI 호출 → docs 목록. 오류 응답은 LookupUnavailable로 올린다.

    SEOJI도 KCI처럼 오류를 HTTP 200으로 준다(2026-08 실측).
        {"RESULT":"ERROR","ERR_CODE":"011","ERR_MESSAGE":"유효하지 않은 인증키 값입니다."}
    이때 docs 키가 아예 없으므로, 그냥 읽으면 '자료 없음'과 구별되지 않아
    키가 죽은 동안 실존 단행본이 전부 '미발견'으로 표시된다.
    자료가 0건일 때는 정상 응답으로 {"TOTAL_COUNT":"0", ..., "docs":[]}가 온다.
    """
    r = _get(client, _NLK_URL, params)
    if r is None:
        return None
    try:
        j = r.json() or {}
    except ValueError:  # 점검 페이지 등 — 조회 못 한 것이지 자료가 없는 게 아니다
        raise LookupUnavailable("SEOJI: 응답이 JSON이 아님")
    if str(j.get("RESULT", "")).upper() == "ERROR":
        raise LookupUnavailable(
            f"SEOJI {j.get('ERR_CODE', '')}: {j.get('ERR_MESSAGE', '')}".strip())
    return j.get("docs") or []


def _nlk_main_title(t: str) -> str:
    """등록 서명에서 대역서명·판차 부기·부제를 떼어낸 주서명."""
    t = re.sub(r"\s+", " ", (t or "")).strip()
    t = t.split(" = ")[0]                        # '국문서명 = English title'
    t = re.sub(r"[(（\[].*?[)）\]]", " ", t)       # '(전면개정판)' 등 부기
    t = re.split(r"\s*[:：]\s*", t)[0]            # 부제
    return re.sub(r"\s+", " ", t).strip(" .,:;-–—")


def _nlk_queries(title: str) -> list[str]:
    """검색어 후보 — 넓은 것 순으로 최대 3개.

    SEOJI의 title 검색은 등록 서명에 검색어가 통째로 들어 있어야 걸리는
    부분 문자열 방식이다(2026-08 실측: '서비스론' 85건, '정보서비스론' 11건).
    그래서 원고 서명이 등록 서명보다 길면 — 부제·판차 부기가 붙었거나 띄어쓰기
    표기가 다르면 — 실존 단행본도 0건이 된다
    ('정보서비스론: 이론과 실제' 0건 / '정보서비스론' 11건).
    걸릴 때까지 줄여 재시도하되, 넓힌 검색어로 얻은 후보도 판정은 원 서명과의
    유사도로 하므로 엉뚱한 책이 통과하지는 않는다.
    """
    out: list[str] = []
    for cand in (title, _nlk_main_title(title)):
        c = re.sub(r"\s+", " ", (cand or "")).strip()
        if len(_norm(c)) >= 3 and c not in out:
            out.append(c)
    words = out[-1].split() if out else []
    for n in (3, 2):                              # 앞 n어절까지 축약
        if len(words) > n:
            c = " ".join(words[:n])
            # 너무 짧은 검색어는 후보만 폭증시키고 정답을 밀어낸다
            if len(_norm(c)) >= 6 and c not in out:
                out.append(c)
    return out[:3]


def nlk_book_by_isbn(client: httpx.Client, isbn: str) -> dict | None:
    """SEOJI ISBN 직접 조회 — 단건 검증(quick)·미매칭 재조회용.

    제목 검색과 달리 ISBN은 유일키라 유사도 판정이 필요 없다.
    붙임표·공백이 섞인 입력을 받으므로 숫자만 남겨 보낸다.
    """
    key = env_get("NLK_CERT_KEY")
    isbn = re.sub(r"[^0-9Xx]", "", isbn or "")
    if not key or len(isbn) not in (10, 13):
        return None
    docs = _nlk_docs(client, {"cert_key": key, "result_style": "json",
                              "page_no": 1, "page_size": 5, "isbn": isbn})
    if not docs:
        return None
    d = docs[0]
    year = re.sub(r"\D", "", (d.get("PUBLISH_PREDATE") or "")
                  or (d.get("REAL_PUBLISH_DATE") or ""))[:4]
    return {
        "title": (d.get("TITLE") or "").strip(),
        "authors": [d.get("AUTHOR") or ""],
        "publisher": d.get("PUBLISHER") or "", "year": year,
        "isbn": (d.get("EA_ISBN") or "") or (d.get("SET_ISBN") or ""),
        "source": "국립중앙도서관", "sim": 1.0,
    }


def nlk_book_search(client: httpx.Client, title: str, author: str = "",
                    year: str = "") -> dict | None:
    key = env_get("NLK_CERT_KEY")
    if not key or not title or len(title) < 3:
        return None
    want_year = re.sub(r"\D", "", year or "")[:4]
    # 저자는 AND 조건이며 부분 일치다. 맞으면 동명 서명의 오매칭을 줄여 주지만
    # 표기가 다르면 실존 단행본도 0건이 되므로, 결과가 없으면 저자를 빼고 다시 본다.
    au = re.sub(r"\s+", " ", (author or "")).strip()
    if len(au) > 20 or re.search(r"\d", au):
        au = ""

    q_main = _nlk_main_title(title)
    use_main = len(_norm(q_main)) >= 6          # 주서명이 너무 짧으면 오매칭 위험

    def pick(docs: list[dict]) -> tuple[dict | None, float]:
        best, best_key = None, (0.0, 0)
        for d in docs:
            raw = (d.get("TITLE") or "").strip()
            sim = _sim(title, raw.split(" = ")[0])
            if use_main:
                # 원고에만 부제가 붙은 경우를 살린다 — 주서명끼리도 대조
                sim = max(sim, _sim(q_main, _nlk_main_title(raw)))
            # 발행예정일이 비어 있는 자료가 있어 실제 발행일로 보완한다
            d_year = re.sub(r"\D", "", (d.get("PUBLISH_PREDATE") or "")
                            or (d.get("REAL_PUBLISH_DATE") or ""))[:4]
            # 같은 서명의 판이 여럿이면(예: '디지털도서관 운영론' 2008·2026)
            # 제목 유사도만으로는 구분되지 않는다. 원고 연도와 맞는 판을 골라야
            # 맞게 쓴 발행연도를 다른 판의 연도로 '교정'하라고 하지 않는다.
            key = (round(sim, 3), 1 if want_year and d_year == want_year else 0)
            if key > best_key:
                best_key = key
                best = {
                    "title": raw, "authors": [d.get("AUTHOR") or ""],
                    "publisher": d.get("PUBLISHER") or "", "year": d_year,
                    "isbn": (d.get("EA_ISBN") or "") or (d.get("SET_ISBN") or ""),
                    "source": "국립중앙도서관",
                }
        return best, best_key[0]

    for i, q in enumerate(_nlk_queries(title)):
        params = {"cert_key": key, "result_style": "json", "page_no": 1,
                  "page_size": 20, "title": q[:60]}
        if au and i == 0:
            params["author"] = au
        docs = _nlk_docs(client, params)
        if docs is None:
            return None
        if not docs and au and i == 0:          # 저자 표기 차이 — 저자를 빼고 재시도
            docs = _nlk_docs(client, {k: v for k, v in params.items() if k != "author"})
            if docs is None:
                return None
        best, best_sim = pick(docs)
        if best and best_sim >= 0.80:
            best["sim"] = best_sim
            return best
    return None


# ---------------------------------------------------------------- 국회도서관(학위논문 등)

def _nanet_clean(v: str) -> str:
    """국회도서관 응답값에서 검색어 강조용 HTML을 걷어낸다.

    검색어와 겹치는 글자에 태그를 입혀서 준다(2026-08 실측):
        발행자: 숭의여자대<font color="red">학교</font> 문헌정보과
    XML상에는 이스케이프돼 있어 파서를 통과하므로, 그대로 두면 태그가 붙은 채로
    화면과 교정 제안에까지 흘러간다.
    """
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", v or "")).strip()


def nanet_search(client: httpx.Client, title: str, year: str = "") -> dict | None:
    key = _service_key("NANET_API_KEY")
    if not key or not title or len(title) < 4:
        return None
    want_year = re.sub(r"\D", "", year or "")[:4]
    # 검색식이 '필드,검색어' 형식이므로 제목의 콤마는 공백으로 치환
    q_title = title[:60].replace(",", " ").strip()
    # 검색항목은 '전체'가 아니라 '자료명'을 쓴다. '전체'는 느슨하게 매칭되어
    # 짧은 서명일수록 수만 건이 걸리고 정답이 상위 10건에 들지 못한다
    # (예: '참고정보서비스론' → 전체 58,024건·매칭 실패 / 자료명 3건·매칭 성공).
    r = _get(client, "https://apis.data.go.kr/9720000/searchservice/basic",
             {"serviceKey": key, "pageno": 1, "displaylines": 10,
              "search": f"자료명,{q_title}"})
    if r is None:
        return None
    root = _xml_root(r.text)
    if root is None:
        return None
    best, best_key = None, (0.0, 0)
    for rec in root.iter("recode"):  # 국회도서관 API의 실제 태그명(recode)
        fields = {}
        for item in rec.iter("item"):
            name = _nanet_clean(item.findtext("name") or "")
            value = _nanet_clean(item.findtext("value") or "")
            if name:
                fields[name] = value
        # 제목 필드명이 자료 유형마다 다르다(2026-08 실측): 학술기사 '기사명',
        # 학위논문 '논문명', 도서 '자료명'. 도서만 걸리던 기존 코드로는
        # 기사·학위논문이 전부 매칭 실패했다.
        t = (fields.get("기사명") or fields.get("논문명")
             or fields.get("자료명") or fields.get("서명") or "")
        # '국문제목 = English title' 대역 제목과 도서의 저자사항 구분자 '/'를 떼어낸다
        t = t.split(" = ")[0].split(" /")[0].strip().rstrip("/").strip()
        sim = _sim(title, t)
        d_year = re.sub(r"\D", "", fields.get("발행년도", "")
                        or fields.get("학위년도", "")
                        or fields.get("발행년", ""))[:4]
        # 같은 제목의 자료가 여러 건일 때 원고 연도에 맞는 것을 고른다. 학위논문이
        # 학술기사로도 실리면 제목이 100% 같은 레코드가 여러 해에 걸쳐 나오는데
        # (변회균 논문: 2014년 학회지 · 2017년 연구지), 앞의 것을 집으면 맞게 쓴
        # 발행연도를 다른 자료의 연도로 '교정'하라고 하게 된다.
        key = (round(sim, 3), 1 if want_year and d_year == want_year else 0)
        if key > best_key:
            best_key = key
            best = {"title": t, "authors": [fields.get("저자명", "")],
                    "year": d_year, "publisher": fields.get("발행자", ""),
                    "source": "국회도서관"}
    if best and best_key[0] >= 0.80:
        best["sim"] = best_key[0]
        return best
    return None


# ---------------------------------------------------------------- RISS(학술연구정보서비스, KERIS)

_RISS_URL = "https://www.riss.kr/openApi"
# 자료유형 코드(2026-09 실측): A 국내학술논문 · T 학위논문(국내 석·박사 + 해외 박사 DDOD) ·
# U 단행본(국내·해외서) · F 연구보고서(응답 riss.type은 G/E) · S 학술지 · O 해외학술논문(미사용)
RISS_TYPE = {"journal": "A", "thesis": "T", "book": "U", "report": "F"}
# 인증 오류(<Error>004</Error> '인증 오류')는 HTTP 200으로 온다. 확인되면 프로세스가 사는
# 동안 호출을 멈춘다 — 죽은 키로 항목마다 헛호출하며 전부 '일시 오류'로 떨어뜨리지 않기
# 위해서다. kr_api_status()["riss"]도 함께 False가 되어 판정 문구가 RISS를 빼고 계산된다.
_RISS_OFF = False
_RISS_JOURNAL_CACHE: dict[str, dict] = {}
_RISS_TRIES = 2  # 폴백·교차 확인 정보원이라 KCI(3회)보다 짧게 끊는다


def riss_enabled() -> bool:
    return bool(env_get("RISS_API_KEY")) and not _RISS_OFF


def _riss_call(client: httpx.Client, params: dict):
    """RISS GET → XML root. None은 호출 안 함(키 없음·중단), 오류는 LookupUnavailable.

    정상 응답은 <head><Error>0</Error><ErrorMessage>No Error</ErrorMessage>이고
    0건일 때도 같다(totalcount만 0). Error가 0이 아닌 값이면 조회 자체가 안 된 것이다.
    간헐적으로 응답 없이 연결이 끊기는 일이 있어(2026-09 실측 40회 중 3회)
    get_with_retry가 한 번 더 보낸다.
    """
    global _RISS_OFF
    key = env_get("RISS_API_KEY")
    if not key or _RISS_OFF:
        return None
    r = http_util.get_with_retry(client, _RISS_URL,
                                 params={"key": key, "version": "1.0", **params},
                                 timeout=_TIMEOUT, tries=_RISS_TRIES)
    if r.status_code != 200:
        # RISS는 '없음'을 언제나 200 + totalcount 0으로 주므로, 다른 상태는 조회 실패다.
        # 학위논문은 RISS가 1순위라 이를 None으로 읽으면 국내 학위논문 전부가 '미발견'이 된다.
        raise LookupUnavailable(f"RISS HTTP {r.status_code}")
    root = _xml_root(r.text)
    if root is None or root.tag != "record":  # 점검 페이지(XHTML) 등 — 자료가 없는 게 아니라 조회를 못 한 것
        raise LookupUnavailable("RISS: 응답이 검색 결과 XML이 아님")
    code = (root.findtext("head/Error") or "").strip()
    if code and code != "0":
        msg = (root.findtext("head/ErrorMessage") or "").strip()
        # 004 '인증 오류'는 잘못된 type 같은 요청 오류에도 같은 코드로 온다(2026-09 실측)
        # — 호출부가 유형을 검증하므로 실제로는 키 문제다. 원인 추적을 위해 파라미터를 남긴다.
        if code == "004" or "인증" in msg:
            _RISS_OFF = True
            _log(f"[RISS] 인증 오류({code} {msg}) — 이후 호출 생략(.env의 RISS_API_KEY 확인 필요; "
                 f"요청 {sorted(k for k in params if k != 'key')})")
        raise LookupUnavailable(f"RISS {code}: {msg}")
    return root


_RISS_PAREN_TAIL = re.compile(r"\s*[(（]([^()（）]*)[)）]\s*$")


def _riss_split_title(raw: str) -> tuple[str, str]:
    """RISS 등록 제목 → (주제목, 병기 영문 제목).

    국내학술논문은 '국문 제목 (English Title)' 또는 '(日本語原題)', 학위논문·단행본은
    '국문 제목 = English Title' 꼴로 원어·영문 제목을 병기한다(2026-09 실측). 병기에
    한글이 없을 때만 떼어낸다 — '(사서교사 미배치 학교를 중심으로)' 같은 국문 부기는
    제목의 일부다. 영문 병기는 국내 논문의 영문 인용을 대조하는 데 쓰고, 일본어·한자
    원제는 대조에 쓰지 않는다.
    """
    t = re.sub(r"\s+", " ", (raw or "")).strip()
    en = ""

    def foreign(s: str) -> bool:
        # 영문 제목(소문자나 띄어쓰기가 있는 로마자) 또는 일본어·한자 원제. 'II'·'(2)'·'(上)'
        # 같은 권차·부기는 제목의 일부이므로 떼어내지 않는다.
        return (bool(re.search(r"[A-Za-z]", s)) and bool(re.search(r"[a-z]|\s", s))) \
            or bool(re.search(r"[぀-ヿ]|[一-鿿]{2,}", s))

    if " = " in t and not _HANGUL_RE.search(t.split(" = ", 1)[1]):
        t, en = [x.strip() for x in t.split(" = ", 1)]
    elif (m := re.search(r"\s:\s+([^가-힣]*[A-Za-z][^가-힣]*)$", t)):
        # 학위논문 레코드는 ' : English Title' 꼴로도 병기한다(2026-09 실측) — '제목 : 국문 부제'는 그대로
        en = m.group(1).strip()
        t = t[:m.start()].strip()
    else:
        m = _RISS_PAREN_TAIL.search(t)
        if m and not _HANGUL_RE.search(m.group(1)) and foreign(m.group(1)):
            en = m.group(1).strip()
            t = t[:m.start()].strip()
    if en and not re.search(r"[A-Za-z]", en):
        en = ""
    # 같은 영문 제목이 두 번 이어 붙은 레코드가 있다(2026-09 실측: 송기호 2009) —
    # 그대로 두면 영문 인용과의 유사도가 반으로 떨어진다
    if (m := re.fullmatch(r"(.{10,}?)\s+\1", en)):
        en = m.group(1)
    return t, en


def _riss_authors(raw: str) -> list[str]:
    """'강봉숙|박주현' → 둘, '이정희(李貞姫)(Jung Hi Lee)' → 이정희, '송기호|Song, Gi-Ho' → 송기호.

    저자 구분자는 '|'인데 같은 사람의 로마자 표기도 '|'로 이어 붙는다(2026-09 실측).
    한글 이름이 하나라도 있으면 로마자 항목은 병기로 보고 버린다. 해외 자료는 전부
    로마자라 그대로 둔다.
    """
    parts = [re.sub(r"[(（][^()（）]*[)）]", "", p).strip() for p in (raw or "").split("|")]
    parts = [p for p in parts if p]
    ko = [p for p in parts if _HANGUL_RE.search(p)]
    return ko or parts


def _author_match(au: str, rec_authors: list[str]) -> bool:
    """원고 제1저자가 레코드 저자 목록에 있는가 — 한글은 이름 전체, 로마자는 성(姓)으로 본다.

    레코드에 저자가 없으면 판단을 유보(True)한다. '홍길동 외'·'Poppe, R. L.'·'Rebecca Poppe'
    처럼 원고 표기가 흔들려도 성·이름 포함 관계로 잡는다.
    """
    if not au or not rec_authors:
        return True
    a = _norm(au)
    if _HANGUL_RE.search(au):
        a = re.sub(r"(외|등)$", "", a)
        return any(a and (a in _norm(x) or _norm(x) in a) for x in rec_authors if _norm(x))
    # 로마자: 'Poppe, R. L.' → poppe / 'Rebecca Poppe' → poppe
    sur = au.split(",", 1)[0] if "," in au else au.split()[-1]
    sur = _norm(sur)
    return any(sur and sur in _norm(x) for x in rec_authors)


def _no_parens(s: str) -> str:
    """제목 안의 괄호 부기 제거 — '방재지도(hazards map)를' → '방재지도를'."""
    return re.sub(r"\s*[(（][^()（）]*[)）]", "", s or "")


def _riss_words(s: str) -> str:
    """검색어용 낱말만 남긴다 — 구두점 토큰이 AND 조건에 끼지 않게."""
    s = re.sub(r"[「」『』\"'“”‘’·:：\-–—,\.\?!()\[\]（）/;=]+", " ", s or "")
    return _q_trim(re.sub(r"\s+", " ", s).strip())


def _riss_queries(title: str) -> list[str]:
    """검색어 후보 — 넓은 것 순으로 최대 3개.

    RISS title 검색은 낱말 AND(어절 앞부분 일치, '연구의'로 '연구'가 걸림)라 구문
    검색이 아니다(2026-09 실측). 낱말이 많을수록 좁아지므로 원고 표기가 등록 표기와
    한 어절만 달라도 0건이 된다 — 전체 → 부제 제거 → 앞 3어절 순으로 줄여 재시도하되,
    판정은 항상 원 제목과의 유사도로 하므로 엉뚱한 문헌이 통과하지는 않는다.
    """
    base, _ = _riss_split_title(title)  # 원고에도 '국문 = English' 병기가 있을 수 있다
    out: list[str] = []
    for cand in (base, _main_title(base)):
        c = _riss_words(cand)
        if len(_norm(c)) >= 4 and c not in out:
            out.append(c)
    words = out[-1].split() if out else []
    if len(words) > 3:
        c = " ".join(words[:3])
        if len(_norm(c)) >= 6 and c not in out:
            out.append(c)
    return out[:3]


def _riss_num(v: str) -> str:
    """권·호 값 정돈 — 없음은 '-'로 온다. 'Vol.12'·'제3호' 꼴이 오면 숫자만 남긴다."""
    v = (v or "").strip()
    if v in ("", "-"):
        return ""
    v = re.sub(r"^(?:vol\.?|no\.?|제)\s*", "", v, flags=re.I)
    return v.strip(" 권호집")


def _riss_degree(mtype: str) -> str:
    """riss.mtype → 원고 표기용 학위명. '국내석사' → 석사학위논문, '해외박사(DDOD)' → Doctoral dissertation."""
    m = mtype or ""
    if m.startswith("해외"):
        return "Doctoral dissertation" if "박사" in m else ("Master's thesis" if "석사" in m else "")
    if "박사" in m:
        return "박사학위논문"
    if "석사" in m:
        return "석사학위논문"
    return ""


def _riss_record(rec, t: str, en: str, year: str, rtype: str) -> dict:
    def g(tag: str) -> str:
        return (rec.findtext(tag) or "").strip()  # 점 있는 태그명은 ET에서 그냥 문자열이다

    mtype = g("riss.mtype")
    out = {
        "title": t, "title_en": en,
        "authors": _riss_authors(g("riss.author")),
        "year": year,
        "container": g("riss.stitle"),            # 학술지명(A) — 학위논문·단행본은 ""
        "volume": _riss_num(g("riss.vol")), "issue": _riss_num(g("riss.no")),
        "pages": "", "doi": "", "isbn": "",       # RISS 스키마에 없음 — 빈 값이어야 교정 제안이 안 나간다
        "publisher": g("riss.publisher"),         # 학위논문은 수여기관(대학교 대학원), 학술지 논문은 학회
        "url": g("url"),                          # 근거 레코드 링크(http://www.riss.kr/link?id=…)
        "reg": g("riss.reg"),                     # 'KCI등재'·'KCI우수등재'·'KCI후보'·'KCI등재,SCOPUS'·''
        "mtype": mtype, "riss_type": g("riss.type") or rtype,
        "source": "RISS",
    }
    if rtype == "T":
        out["institution"] = out["publisher"]
        out["degree"] = _riss_degree(mtype)
    return out


def riss_search(client: httpx.Client, title: str, author: str = "", year: str = "",
                rtype: str = "A") -> dict | None:
    """RISS 검색 → 최고 유사도 매칭 dict 또는 None. 키·응답 오류는 LookupUnavailable.

    rtype은 RISS_TYPE 값(A 학술논문 / T 학위논문 / U 단행본 / F 연구보고서). 반환 dict는
    verify_kr 가족(KCI·SEOJI·NANET)과 같은 평면 서지 + url·reg·mtype. KCI 전용 키
    (kci_id·authors_en)는 넣지 않는다 — verify.py가 그 키를 보고 KCI 상세 조회·'KCI 등록
    표기' 안내를 붙이기 때문이다.
    """
    if not riss_enabled() or not title or len(title) < 4 or rtype not in RISS_TYPE.values():
        return None
    want_year = re.sub(r"\D", "", year or "")[:4]
    # 저자는 AND 조건 — 맞으면 동명 제목의 오매칭을 줄이지만 표기가 다르면 0건이 되므로
    # 첫 질의에만 붙이고, 0건이면 저자 없이 다시 본다(SEOJI와 같은 규칙)
    au = re.sub(r"\s+", " ", re.sub(r"[(（][^()（）]*[)）]", "", author or "")).strip()
    if len(au) > 40 or re.search(r"\d", au) or au in ("외", "등", "et al", "et al."):
        au = ""
    queries = _riss_queries(title)
    if not queries:
        return None

    def run(params: dict) -> tuple[dict | None, float, int]:
        root = _riss_call(client, params)
        if root is None:
            return None, 0.0, 0
        total = int(re.sub(r"\D", "", root.findtext("head/totalcount") or "0") or 0)
        best, best_key = None, (0.0, 0, 0)
        for rec in root.iter("metadata"):
            t, en = _riss_split_title(rec.findtext("riss.title") or "")
            # 국문·영문 제목 중 높은 쪽, 부제 생략 관행 흡수 — KCI 매칭과 같은 규칙.
            # 제목 안의 괄호 부기('방재지도(hazards map)를 …')는 원고가 흔히 생략하므로
            # 부기를 뗀 꼴로도 비교한다(2026-09 실측: 부기 포함 비교는 0.75로 문턱 아래).
            sim = max(_sim(title, t), _sim(title, en),
                      _sim(_main_title(title), _main_title(t)),
                      _sim(_main_title(title), _main_title(en)),
                      _sim(_no_parens(title), _no_parens(t)))
            d_year = re.sub(r"\D", "", rec.findtext("riss.pubdate") or "")[:4]
            # 같은 제목이 여러 해·여러 대학에 걸쳐 있으면 저자가 맞고 연도가 맞는 것을 고른다.
            # 저자를 빼고 재검색한 뒤에도 저자 일치를 순위에 넣지 않으면, 흔한 서명(예:
            # '도서관 경영론')에 저자를 잘못 쓴 인용이 다른 사람의 책으로 '확인'된다(2026-09 실측).
            a_ok = _author_match(au, _riss_authors(rec.findtext("riss.author") or ""))
            k = (round(sim, 3), 1 if a_ok else 0, 1 if want_year and d_year == want_year else 0)
            if k > best_key:
                best_key, best = k, _riss_record(rec, t, en, d_year, rtype)
                best["author_mismatch"] = bool(au) and not a_ok
        return best, best_key[0], total

    def hit(best: dict | None, sim: float) -> dict | None:
        if best and sim >= 0.80:
            best["sim"] = sim
            return best
        return None

    full_total = -1  # 전체 제목 질의가 연도 창 안에서 몇 건이었나
    any_total = 0
    year_params = {}
    if want_year.isdigit():
        # 연도 ±1 창 — 학위논문의 학위년도/발행년도 차이와 한 해 오기를 흡수한다
        year_params = {"spubdate": str(int(want_year) - 1), "epubdate": str(int(want_year) + 1)}
    for i, q in enumerate(queries):
        params = {"type": rtype, "title": q, "rowcount": 10, **year_params}
        if i == 0 and au:
            params["author"] = au
        best, sim, total = run(params)
        if total == 0 and i == 0 and au:
            params.pop("author")
            best, sim, total = run(params)
        if i == 0:
            full_total = total
        any_total += total
        if (found := hit(best, sim)):
            return found
    if year_params and full_total == 0:
        # 전체 제목이 연도 창 안에 한 건도 없었다 — 2년 이상 틀린 연도를 잡기 위해
        # 연도 없이 한 번 더 본다(적중하면 연도 교정 제안으로 이어진다). 줄인 질의가
        # 창 안에서 다른 문헌을 돌려줬더라도 전체 제목 기준으로 판단한다.
        best, sim, total = run({"type": rtype, "title": queries[0], "rowcount": 10})
        any_total += total
        if (found := hit(best, sim)):
            return found
    if au and any_total == 0:
        # 제목 질의가 전부 0건 — 앞 어절에 오타가 있거나 등록 제목에 괄호 부기가 끼어
        # ('방재지도(hazards map)를' vs 원고 '방재지도를') 낱말 AND 검색이 모두 비는 경우.
        # 저자+연도 창으로 후보를 받아 제목 유사도로 판정한다(호출 +1, 0.80 문턱은 동일).
        # 흔한 이름은 연도 창 안에서도 수십 건이라 최대치(100)로 받는다.
        best, sim, _ = run({"type": rtype, "author": au, "rowcount": 100, **year_params})
        if (found := hit(best, sim)):
            return found
    return None


def riss_journal_status(client: httpx.Client, journal_name: str) -> dict | None:
    """학술지명으로 RISS 학술지(S) 레코드 조회 → {reg, issn, title, url} / {}(미검색) / None(조회 불가).

    KCI journalSearch가 미승인이라(kci_journal_status 참조) 학술지 등재 여부를 RISS의
    riss.reg('KCI등재'·'KCI우수등재'·'KCI후보'·''·'KCI등재,SCOPUS')로 확인한다. 학술지명은
    '한국도서관·정보학회지'/'한국도서관정보학회지'처럼 가운뎃점 유무가 섞여 오지만
    _sim이 구두점을 지우므로 0.85 문턱으로 같은 학술지로 잡힌다(2026-09 실측).
    조회 실패(None)는 캐시하지 않는다.
    """
    jn = re.sub(r"\s+", " ", (journal_name or "")).strip()
    if not riss_enabled() or len(_norm(jn)) < 3:
        return None
    with _CACHE_LOCK:
        if jn in _RISS_JOURNAL_CACHE:
            return _RISS_JOURNAL_CACHE[jn]
    try:
        root = _riss_call(client, {"type": "S", "title": _riss_words(jn), "rowcount": 10})
    except LookupUnavailable:
        return None  # 부가 정보라 예외로 올리지 않는다 — 판정 자체를 막지 않기 위해
    if root is None:
        return None
    result: dict = {}
    best_sim = 0.0
    for rec in root.iter("metadata"):
        t, _ = _riss_split_title(rec.findtext("riss.title") or "")
        sim = _sim(jn, t)
        if sim > best_sim:
            best_sim = sim
            result = {"title": t, "reg": (rec.findtext("riss.reg") or "").strip(),
                      "issn": (rec.findtext("riss.issn") or "").strip(),
                      "url": (rec.findtext("url") or "").strip()}
    if best_sim < 0.85:
        result = {}
    with _CACHE_LOCK:
        _RISS_JOURNAL_CACHE[jn] = result
    return result


def riss_reg_label(reg: str) -> str:
    """riss.reg → KCI 등재구분 표기('KCI등재,SCOPUS' → '등재(SCOPUS)'). 비어 있으면 ''."""
    parts = [p.strip() for p in (reg or "").split(",") if p.strip()]
    kci = [p for p in parts if p.upper().startswith("KCI")]
    other = [p for p in parts if not p.upper().startswith("KCI")]
    label = ""
    if kci:
        label = kci[0][3:].strip()  # 'KCI등재' → '등재', 'KCI우수등재' → '우수등재', 'KCI후보' → '후보'
        if label == "후보":
            label = "등재후보"
    if other:
        label = (label + "(" + "·".join(other) + ")") if label else "·".join(other)
    return label


# ---------------------------------------------------------------- 국가법령정보센터(법제처)
# 법령 목록 조회 Open API(open.law.go.kr, 국가법령정보 공동활용) — 법령의 실존, 현행 공포번호·
# 공포일·시행일, 영어번역 법령명(target=elaw)을 준다. 참고문헌의 법령 항목('독서문화진흥법.
# 법률 제21447호.')을 대조하고 영문 변환 목록에 공식 영문 법령명(READING CULTURE PROMOTION
# ACT → Reading Culture Promotion Act)을 쓰기 위한 것(사용자 요청 2026-09-11).
LAW_SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
_LAW_TAG_RE = re.compile(r"<[^>]+>")   # 검색어 강조 <strong> 태그가 법령명 안에 섞여 온다
_LAW_SMALL_WORDS = {"a", "an", "the", "and", "or", "of", "in", "on", "for", "to", "with", "at",
                    "by", "from", "as"}


def law_oc() -> str:
    """공동활용 OC(신청자의 이메일 ID). 미설정이면 활용 안내서가 예시로 쓰는 'test'.

    운영 서버는 open.law.go.kr에서 공동활용을 신청하고(서버 IP·도메인 등록) .env LAW_OC에
    자기 ID를 두는 것이 원칙이다 — 예시 계정이 막히면 법령만 '확인 못 함'이 된다.
    """
    return env_get("LAW_OC").strip() or "test"


def _law_clean(s: str) -> str:
    return re.sub(r"\s+", " ", _LAW_TAG_RE.sub("", s or "")).strip()


def _law_key(name: str) -> str:
    """법령명 비교 키 — 공백·괄호 안 부기 제거, 「」 벗기기."""
    s = _law_clean(name)
    s = re.sub(r"[「」『』\"'“”‘’]", "", s)
    s = re.sub(r"\([^)]*\)", "", s)
    return re.sub(r"\s+", "", s)


def _law_date(yyyymmdd: str) -> str:
    """'20260305' → '2026. 3. 5.'(국문 참고문헌의 날짜 표기)."""
    d = re.sub(r"\D", "", yyyymmdd or "")
    if len(d) != 8:
        return yyyymmdd or ""
    return f"{int(d[:4])}. {int(d[4:6])}. {int(d[6:])}."


def law_title_case(s: str) -> str:
    """'ENFORCEMENT DECREE OF THE READING CULTURE PROMOTION ACT' → 'Enforcement Decree of the
    Reading Culture Promotion Act'. 법제처 영어번역 법령명은 전부 대문자로 등록돼 있다."""
    words = _law_clean(s).lower().split()
    out = []
    for i, w in enumerate(words):
        if i > 0 and w in _LAW_SMALL_WORDS:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def _law_items(client: httpx.Client, target: str, query: str) -> list[dict]:
    """lawSearch.do 한 번 — 결과 목록. OC 오류(HTTP 200 + <Response>)는 LookupUnavailable."""
    r = _get(client, LAW_SEARCH_URL,
             {"OC": law_oc(), "target": target, "type": "XML", "query": query, "display": 20})
    if r is None:
        return []
    root = _xml_root(r.text)
    if root is None:
        raise LookupUnavailable("국가법령정보센터 응답을 읽지 못함")
    if root.tag != "LawSearch":
        msg = (root.findtext("result") or root.findtext("msg") or root.tag or "").strip()
        raise LookupUnavailable(f"국가법령정보센터: {msg[:80]}")
    out = []
    for el in root.iter("law"):
        g = lambda tag: _law_clean(el.findtext(tag) or "")
        out.append({
            "name": g("법령명한글"), "abbr": g("법령약칭명"), "name_en": g("법령명영문"),
            "law_id": g("법령ID"),
            "kind": g("법령구분명"), "no": g("공포번호"), "date": g("공포일자"),
            "eff": g("시행일자"), "amend": g("제개정구분명"), "dept": g("소관부처명"),
            "hist": g("현행연혁코드"), "seq": g("법령일련번호"),
        })
    return out


def law_search(client: httpx.Client, name: str) -> dict | None:
    """법령명으로 현행 법령 1건을 찾는다 — 실존·현행 공포번호·시행일 + 영문 법령명.

    반환: {"name", "kind", "no", "date"(공포일), "eff"(시행일), "amend", "dept", "law_id",
           "no_label"('법률 제21447호'), "name_en"('Reading Culture Promotion Act' 또는 ''),
           "url"(국가법령정보센터 법령 페이지), "en_url"(영어번역 검색 페이지)}
    None: 같은 이름의 법령 없음. LookupUnavailable: 조회 실패(OC 오류·네트워크).
    '독서문화진흥법 시행령'처럼 시행령·시행규칙까지 이름이 정확히 같아야 잡는다 — 이름 앞부분만
    같은 법령(시행령)을 본법으로 오인하지 않기 위해.
    """
    key = _law_key(name)
    if len(key) < 2:
        return None
    query = _law_clean(re.sub(r"[「」『』]", "", name))
    items = _law_items(client, "law", query)
    if not items and " " in query:
        # '독서문화 진흥법'처럼 띄어쓰기가 등록명과 다르면 검색이 비는 수가 있다 — 붙여서 한 번 더
        items = _law_items(client, "law", query.replace(" ", ""))
    exact = [it for it in items if _law_key(it["name"]) == key]
    by_abbr = False
    if not exact:  # 약칭('학교도서관법')으로 적은 원고 — 정식 명칭('학교도서관진흥법')을 찾아 준다
        exact = [it for it in items if it.get("abbr") and _law_key(it["abbr"]) == key]
        by_abbr = bool(exact)
    if not exact:
        return None
    exact.sort(key=lambda it: (it["hist"] != "현행", -int(it["date"] or 0)))
    law = exact[0]
    law["by_abbr"] = by_abbr
    en = ""
    try:  # 영문명은 부가 정보 — 실패해도 실존 판정은 유지. 정식 명칭으로 다시 찾는다(약칭 대비)
        for it in _law_items(client, "elaw", law["name"]):
            if _law_key(it["name"]) == _law_key(law["name"]) and it.get("name_en"):
                en = law_title_case(it["name_en"])
                break
    except LookupUnavailable:
        pass
    law["name_en"] = en
    law["no_label"] = f"{law['kind']} 제{law['no']}호" if law["no"] else ""
    law["url"] = "https://www.law.go.kr/법령/" + law["name"]
    law["en_url"] = ("https://www.law.go.kr/engLsSc.do?menuId=1&subMenuId=21&tabMenuId=117&query="
                     + law["name"])
    return law
