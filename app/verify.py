# -*- coding: utf-8 -*-
"""실존·윤리 검증 엔진.

검증 소스(전부 무료 API):
- Crossref: DOI 조회·서지 검색 + 철회/정정/우려표명(updated-by) + 프리프린트 관계
- OpenAlex, DataCite, Semantic Scholar: Crossref 실패 시 폴백 체인
- DOAJ / OpenAlex source / KCI / RISS: 학술지 신뢰성(등재 여부) 확인
- KCI·RISS·국립중앙도서관·국회도서관(verify_kr): 국내 문헌 검증(키 설정 시)
  · 학술지 논문: KCI → (적중 시 RISS 교차 확인) / 미적중 시 RISS → Crossref 폴백
  · 학위논문: RISS(국내·해외) → 국회도서관 폴백
  · 단행본: 국립중앙도서관 → 카카오 책 → 국회도서관 → RISS 폴백 (보고서는 카카오 제외)
  · 해외 단행본: 카카오 책(국내 유통본) — 없으면 오프라인 자료로 생략
- URL 생존 확인

결과 dict:
{status, detail, found_doi, source, retraction, journal, preprint, meta, xref}
- status: verified|mismatch|not_found|suspect|link_ok|link_dead|skipped
- retraction: {type, label, date} | None
- journal: {flag: ok|warn|unknown, detail} | None
- preprint: {published_doi, detail} | None
- meta: 매칭된 문헌의 정규 서지(교정 제안용) | None
- xref: {source, state: agree|differ, url, diff: [str]} | None — 제2 정보원 교차 확인 결과
"""
import difflib
import html
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import httpx

import http_util
import verify_kr
from http_util import LookupUnavailable  # 재수출 — 기존 verify.LookupUnavailable 참조 유지

_HEADERS = {"User-Agent": "RefStd-Agent/2.0 (mailto:park51566@jnu.ac.kr)"}
_TIMEOUT = 12
_CACHE_LOCK = threading.Lock()
_HANGUL_RE = re.compile(r"[가-힣]")


def _get_with_retry(client: httpx.Client, url: str, *, params=None) -> httpx.Response:
    """GET + 429/5xx 재시도. 최종 실패 시 LookupUnavailable. (공통 규약은 http_util 참조)"""
    return http_util.get_with_retry(client, url, params=params,
                                    headers=_HEADERS, timeout=_TIMEOUT)

_PREPRINT_DOI_PREFIX = {
    "10.48550": "arXiv", "10.2139": "SSRN", "10.1101": "bioRxiv/medRxiv",
    "10.31219": "OSF", "10.20944": "Preprints.org", "10.21203": "Research Square",
    "10.31235": "SocArXiv",
}
_UPDATE_LABEL = {
    "retraction": "철회(Retraction)", "retracted": "철회(Retraction)",
    "withdrawal": "철회(Withdrawal)", "removal": "삭제(Removal)",
    "correction": "정정(Correction)", "corrigendum": "정정(Corrigendum)",
    "erratum": "정정(Erratum)",
    "expression_of_concern": "우려표명(Expression of Concern)",
    "new_edition": "개정판", "new_version": "새 버전",
}
_SEVERE_UPDATES = {"retraction", "retracted", "withdrawal", "removal"}


def _norm_title(s: str) -> str:
    # 한글·한자·가나·키릴·그리스 등 주요 문자 유지(비수록 문자로 제목 전체가 사라지는 것 방지)
    s = re.sub(r"[^0-9A-Za-z가-힣一-鿿぀-ゟ゠-ヿ・ー０-９Ａ-Ｚａ-ｚЀ-ӿΑ-Ωα-ω]+", " ",
               (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _similarity(a: str, b: str) -> float:
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        # 정규화로 전부 사라진 문자 체계 — 원문 기준으로 폴백 비교
        ra = re.sub(r"\s+", "", (a or "").casefold())
        rb = re.sub(r"\s+", "", (b or "").casefold())
        if not ra or not rb:
            return 0.0
        return difflib.SequenceMatcher(None, ra, rb).ratio()
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _crossref_titles(m: dict) -> list[str]:
    """Crossref title (+subtitle 결합) 비교 후보들.

    Crossref 문자열에는 '&amp;' 같은 HTML 엔티티가 섞여 온다(2026-09 실측:
    Library &amp; Information Science Research) — 풀지 않으면 유사도가 깎이고
    화면 대조표에 부호가 그대로 노출된다.
    """
    title = html.unescape(" ".join(m.get("title") or []))
    subtitle = html.unescape(" ".join(m.get("subtitle") or []))
    variants = [title]
    if subtitle:
        variants.append(f"{title}: {subtitle}")
    return [v for v in variants if v]


def _best_sim(entry_title: str, m: dict) -> float:
    return max((_similarity(entry_title, v) for v in _crossref_titles(m)), default=0.0)


def _base_result() -> dict:
    return {"status": "skipped", "detail": "", "found_doi": "", "source": "",
            "retraction": None, "journal": None, "preprint": None, "meta": None,
            "xref": None}


# ================================================================ 소스별 클라이언트

def _crossref_by_doi(client: httpx.Client, doi: str) -> dict | None:
    try:
        r = _get_with_retry(client, f"https://api.crossref.org/works/{quote(doi, safe='')}")
        if r.status_code == 200:
            return r.json().get("message")
    except ValueError:
        pass
    return None


def _crossref_search(client: httpx.Client, entry: dict, ignore_year: bool = False,
                     threshold: float = 0.82) -> dict | None:
    title = entry.get("title", "")
    if not title or len(title) < 8:
        return None
    authors = entry.get("authors") or []
    q = title + (" " + authors[0] if authors else "")
    try:
        r = _get_with_retry(client, "https://api.crossref.org/works",
                            params={"query.bibliographic": q, "rows": 5})
        if r.status_code != 200:
            return None
        items = r.json().get("message", {}).get("items", [])
    except ValueError:
        return None
    year_m = re.match(r"(\d{4})", entry.get("year") or "")
    want_year = int(year_m.group(1)) if year_m else None
    want_vol = (entry.get("volume") or "").strip()
    want_iss = (entry.get("issue") or "").strip()
    want_fp = re.match(r"\d+", (entry.get("pages") or "").strip())
    want_fp = want_fp.group(0) if want_fp else ""

    def _anchors(it) -> tuple[int, int]:
        """(대조 가능한 권·호·면수 개수, 일치 개수) — 동일 제목 서신·정정과 원 논문 구별용."""
        anchors = hits = 0
        if want_vol:
            anchors += 1
            hits += (it.get("volume") or "") == want_vol
        if want_iss:
            anchors += 1
            hits += (it.get("issue") or "") == want_iss
        if want_fp:
            anchors += 1
            it_fp = re.match(r"\d+", (it.get("page") or ""))
            hits += bool(it_fp) and it_fp.group(0) == want_fp
        return anchors, hits

    best, best_score, best_sim = None, 0.0, 0.0
    for it in items:
        sim = _best_sim(title, it)
        if sim < threshold:
            continue
        if want_year and not ignore_year:
            parts = (it.get("issued") or {}).get("date-parts") or [[None]]
            it_year = parts[0][0]
            if it_year and abs(int(it_year) - want_year) > 1:
                continue
        anchors, hits = _anchors(it)
        if ignore_year and anchors >= 2 and hits == 0:
            continue  # 연도도 다르고 권·면수도 전부 다르면 다른 문서로 간주
        score = sim + 0.05 * hits
        if score > best_score:
            best, best_score, best_sim = it, score, sim
    if best:
        best["_sim"] = best_sim
        return best
    return None


def _openalex_by_doi(client: httpx.Client, doi: str) -> dict | None:
    try:
        r = _get_with_retry(client, f"https://api.openalex.org/works/doi:{quote(doi, safe='')}")
        if r.status_code == 200:
            return r.json()
    except ValueError:
        pass
    return None


def _openalex_search(client: httpx.Client, entry: dict) -> dict | None:
    title = entry.get("title", "")
    if not title or len(title) < 8:
        return None
    try:
        r = _get_with_retry(client, "https://api.openalex.org/works",
                            params={"search": title[:200], "per-page": 5})
        if r.status_code != 200:
            return None
        items = r.json().get("results", [])
    except ValueError:
        return None
    year_m = re.match(r"(\d{4})", entry.get("year") or "")
    want_year = int(year_m.group(1)) if year_m else None
    best, best_sim = None, 0.0
    for it in items:
        sim = _similarity(title, it.get("title") or "")
        if want_year and it.get("publication_year") and abs(it["publication_year"] - want_year) > 1:
            continue
        if sim > best_sim:
            best, best_sim = it, sim
    if best and best_sim >= 0.85:
        best["_sim"] = best_sim
        return best
    return None


def _datacite_by_doi(client: httpx.Client, doi: str) -> dict | None:
    try:
        r = _get_with_retry(client, f"https://api.datacite.org/dois/{quote(doi, safe='')}")
        if r.status_code == 200:
            return (r.json().get("data") or {}).get("attributes")
    except ValueError:
        pass
    return None


def _eric_search(client: httpx.Client, entry: dict) -> dict | None:
    """ERIC(미국 교육학 DB, 무료·키 불요) 검색.

    School Library Research 등 DOI 없는 교육·문헌정보 학술지는 Crossref·OpenAlex·
    Semantic Scholar 어디에도 색인되지 않아 실존 논문이 '실존 의심'으로 몰렸다
    (2026-09 실측: Thompson 외 2021 → 3개 DB 미수록, ERIC EJ1292860에서 확인).
    따옴표 구문 검색은 0건을 돌려주므로(실측) 구두점을 걷어낸 낱말 질의 뒤
    유사도·연도로 판정한다. 제목의 &apos; 같은 HTML 엔티티는 풀어서 비교한다.
    """
    title = entry.get("title", "")
    if not title or len(title) < 8:
        return None
    q = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", title)).strip()
    if len(q) > 120:
        q = q[:120]
        if " " in q:
            q = q[: q.rfind(" ")]
    try:
        r = _get_with_retry(client, "https://api.ies.ed.gov/eric/",
                            params={"search": f"title:{q}", "format": "json", "rows": 8,
                                    # 기본 응답에는 학술지명(source)·권호면수(sourceid)가 없다(2026-09-11 실측)
                                    "fields": "id,title,author,source,sourceid,publicationdateyear,"
                                              "issn,url,publicationtype"})
        if r.status_code != 200:
            return None
        docs = (r.json().get("response") or {}).get("docs") or []
    except ValueError:
        return None
    year_m = re.match(r"(\d{4})", entry.get("year") or "")
    want_year = int(year_m.group(1)) if year_m else None
    best, best_sim = None, 0.0
    for d in docs:
        sim = _similarity(title, html.unescape(d.get("title") or ""))
        dy = d.get("publicationdateyear")
        if want_year and isinstance(dy, int) and abs(dy - want_year) > 1:
            continue
        if sim > best_sim:
            best, best_sim = d, sim
    if best and best_sim >= 0.85:
        best["_sim"] = best_sim
        return best
    return None


# v·n·p 뒤에 숫자가 와야 권·호·면수다 — 'Nov 1999'의 'Nov'를 호로 읽지 않게
_ERIC_SRCID_RE = re.compile(r"(?:\bv(?P<v>\d[\w\-]*))?\s*(?:\bn(?P<n>\d[\w\-]*))?\s*(?:\bp(?P<p1>\d+)(?:-(?P<p2>\d+))?)?")


def _parse_eric_sourceid(s: str) -> dict:
    """ERIC sourceid 'v43 n9 p626-39 May 1990' → {volume: 43, issue: 9, pages: 626-639}.

    ERIC은 끝 면수를 앞자리를 떼고 적는다(626-39) — 앞 면수의 자릿수로 되살린다.
    권 없이 통권만 있는 학술지는 'n104 p1-9'처럼 온다.
    """
    out = {"volume": "", "issue": "", "pages": ""}
    s = (s or "").strip()
    if not s:
        return out
    m = _ERIC_SRCID_RE.search(s)
    if not m:
        return out
    out["volume"] = m.group("v") or ""
    out["issue"] = m.group("n") or ""
    p1, p2 = m.group("p1"), m.group("p2")
    if p1 and p2:
        if len(p2) < len(p1):
            p2 = p1[:len(p1) - len(p2)] + p2
        out["pages"] = f"{p1}-{p2}"
    elif p1:
        out["pages"] = p1
    return out


def _meta_from_eric(d: dict) -> dict:
    src = d.get("source")
    bib = _parse_eric_sourceid(d.get("sourceid") or "")
    return {
        "title": html.unescape(d.get("title") or "").rstrip("."),
        "container": html.unescape(src) if isinstance(src, str) else "",
        "year": str(d.get("publicationdateyear") or ""),
        "volume": bib["volume"] or (str(d.get("volume")) if d.get("volume") else ""),
        "issue": bib["issue"], "pages": bib["pages"], "doi": "", "publisher": "", "isbn": "",
        "authors": d.get("author") or [],
        "eric_id": d.get("id") or "",
        "source": "ERIC",
    }


def _eric_print_crosscheck(client: httpx.Client, entry: dict, result: dict) -> None:
    """Crossref로 실존을 확인한 서양 학술지 논문의 권·호·면수를 ERIC 인쇄본 서지와 교차 확인.

    Crossref 등록은 출판사 온라인 체계를 따라 인쇄본과 어긋나기도 한다(2026-09-11 실측:
    McKenna & Kear 1990, The Reading Teacher — Crossref 43(8), ERIC·JSTOR 43(9), DOI도
    rt.43.8.3). 참고문헌은 인쇄본 서지가 기준이므로 두 정보원이 갈리면 원고가 어느 쪽과
    같은지 보고, 원고와 같은 값은 교정 제안하지 않는다. 둘 다 원고와 다르면 인쇄본(ERIC)을
    권한다. ERIC 미수록·조회 실패는 조용히 넘긴다(부가 확인).
    """
    meta = result.get("meta")
    if not meta or not any(entry.get(k) for k in ("volume", "issue", "pages")):
        return
    er, _err = _safe(_eric_search, client, entry)
    if not er:
        return
    bib = _parse_eric_sourceid(er.get("sourceid") or "")
    if not any(bib.values()):
        return
    notes = []
    for k, label in (("volume", "권"), ("issue", "호"), ("pages", "면수")):
        ev, cv, mine = bib.get(k, ""), (meta.get(k) or ""), (entry.get(k) or "")
        nz = lambda x: re.sub(r"\D", "", x or "")
        if not ev:
            continue
        if not cv:
            meta[k] = ev          # Crossref에 없는 항목은 ERIC으로 채운다
            continue
        if nz(ev) == nz(cv):
            continue
        # 두 정보원이 갈린다 — 원고와 같은 쪽을 기준으로 삼아 헛제안을 막는다
        if nz(mine) == nz(ev):
            meta[k] = ev
            notes.append(f"{label} Crossref {cv} / ERIC(인쇄본) {ev} — 원고와 같은 ERIC 기준")
        elif nz(mine) == nz(cv):
            notes.append(f"{label} Crossref {cv} / ERIC(인쇄본) {ev} — 원고와 같은 Crossref 기준")
        else:
            meta[k] = ev
            notes.append(f"{label} Crossref {cv} / ERIC(인쇄본) {ev} — 인쇄본 기준으로 제안")
    if er.get("id"):
        meta["eric_id"] = er["id"]
    if notes:
        meta["source"] = "Crossref·ERIC"
        result["detail"] += " · " + " · ".join(notes)


def _s2_match(client: httpx.Client, entry: dict) -> dict | None:
    title = entry.get("title", "")
    if not title or len(title) < 8:
        return None
    try:
        r = _get_with_retry(client, "https://api.semanticscholar.org/graph/v1/paper/search/match",
                            params={"query": title[:200],
                                    "fields": "title,year,externalIds,venue"})
        if r.status_code != 200:
            return None
        data = r.json().get("data") or []
    except ValueError:
        return None
    if not data:
        return None
    best = data[0]
    sim = _similarity(title, best.get("title") or "")
    if sim >= 0.85:
        best["_sim"] = sim
        return best
    return None


# ================================================================ 메타데이터 정규화

def clean_pages(s: str) -> str:
    """정보원의 면수 표기를 '382-386' 꼴로 — 깨진 구분자·대시 이형·'--'를 하이픈 하나로.

    Crossref에는 출판사가 잘못 올린 '382???386'(엔대시가 깨진 것) 같은 값이 그대로 실려
    있다(2026-09-11 실측: Lynn 1986, Nursing Research — Ovid 원문은 382-386). 숫자 사이의
    글자·숫자가 아닌 문자 뭉치는 모두 구분자로 본다(사용자 확정: 고쳐서 제안).
    """
    s = (s or "").strip()
    if not s:
        return ""
    s = s.replace("–", "-").replace("—", "-").replace("--", "-")
    s = re.sub(r"(?<=\d)[^0-9A-Za-z]+(?=\d)", "-", s)
    return re.sub(r"\s+", "", s)


def _meta_from_crossref(m: dict) -> dict:
    parts = (m.get("issued") or {}).get("date-parts") or [[None]]
    isbns = m.get("ISBN") or []
    # 저자는 교정 제안 대상이 아니라 단건 검증(quick)의 서지 완성용 — 화면 대조표는
    # META_FIELDS만 읽으므로 여기 실어도 기존 표시에는 영향이 없다.
    authors = []
    # 대형 공동연구는 저자가 수천 명이다 — 서지 완성 용도로는 앞쪽이면 충분하다
    for a in (m.get("author") or [])[:30]:
        if a.get("family"):
            authors.append(", ".join(x for x in (a.get("family"), a.get("given")) if x))
        elif a.get("name"):  # 단체 저자
            authors.append(a["name"])
    return {
        # HTML 엔티티 해제 — 'Library &amp; Information Science Research'가 부호째
        # 대조표에 노출되어 맞게 쓴 원고가 불일치로 칠해졌다(2026-09 실측)
        "title": html.unescape(" ".join(m.get("title") or [])),
        "container": html.unescape(" ".join(m.get("container-title") or [])),
        "year": str(parts[0][0] or ""),
        "volume": m.get("volume", "") or "",
        "issue": m.get("issue", "") or "",
        # 면수 없는 온라인 학술지는 article-number가 면수 자리를 대신한다(APA 7 준용)
        "pages": clean_pages(m.get("page") or m.get("article-number") or ""),
        "doi": m.get("DOI", "") or "",
        "publisher": html.unescape(m.get("publisher", "") or ""),
        "isbn": (isbns[0] if isbns else ""),
        "authors": authors,
        "source": "Crossref",
    }


def _meta_from_openalex(w: dict) -> dict:
    biblio = w.get("biblio") or {}
    loc = (w.get("primary_location") or {}).get("source") or {}
    pages = ""
    if biblio.get("first_page"):
        pages = biblio["first_page"] + (f"-{biblio['last_page']}" if biblio.get("last_page") else "")
    return {
        "title": html.unescape(w.get("title") or ""),
        "container": html.unescape(loc.get("display_name") or ""),
        "year": str(w.get("publication_year") or ""),
        "volume": biblio.get("volume") or "",
        "issue": biblio.get("issue") or "",
        "pages": pages,
        "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
        # OpenAlex의 host_organization은 학술지 발행처라 단행본 출판사와 뜻이 달라 쓰지 않는다
        "publisher": "", "isbn": "",
        "authors": [(au.get("author") or {}).get("display_name", "")
                    for au in (w.get("authorships") or [])[:30]
                    if (au.get("author") or {}).get("display_name")],
        "source": "OpenAlex",
    }


def _meta_from_kr(m: dict) -> dict:
    return {
        "title": m.get("title", ""), "container": m.get("container", ""),
        "year": m.get("year", ""), "volume": m.get("volume", ""),
        "issue": m.get("issue", ""), "pages": m.get("pages", ""),
        "doi": m.get("doi", ""),
        # 단행본은 출판사가 서지의 핵심 요소다(문편협 기준 필수 항목).
        # ISBN은 원고에 적지 않는 항목이라 교정 대상이 아니라 확인용으로만 싣는다.
        "publisher": m.get("publisher", ""), "isbn": m.get("isbn", ""),
        # KCI에 저자가 등록한 공식 영문 제목·저자명 — 영문화 목록을 지어내지 않게 한다.
        # 화면 대조표는 META_FIELDS만 읽으므로 여기 실어도 표시에 영향이 없다.
        "title_en": m.get("title_en", ""), "authors_en": m.get("authors_en") or [],
        "authors": m.get("authors") or [],
        # KCI 논문 상세 페이지 링크용 Control Number — 화면이 '근거 레코드' 링크를 만든다
        "kci_id": m.get("kci_id", ""),
        # RISS 레코드 링크(link?id=…)·학위논문 수여기관·학위명 — RISS 적중에만 값이 있다.
        # 없으면 빈 값이라 KCI·SEOJI·NANET 적중의 화면·교정 제안은 달라지지 않는다.
        "url": m.get("url", ""),
        "institution": m.get("institution", ""), "degree": m.get("degree", ""),
        "source": m.get("source", ""),
    }


def _meta_kr_for_entry(entry: dict, kci: dict) -> dict:
    """KCI 서지를 대조표용으로 — 인용 표기 언어에 맞는 제목으로 비교한다.

    국내 논문의 영문 인용(로마자 제목)에 KCI 국문 제목을 '공식'으로 들이대면
    맞게 쓴 영문 표기가 불일치처럼 보인다(2026-09 실측). 영문 인용에는 KCI에
    등록된 공식 영문 제목으로 바꿔 실어 영문↔영문으로 대조되게 한다.
    """
    meta = _meta_from_kr(kci)
    if meta.get("title_en") and not _HANGUL_RE.search(entry.get("title", "")):
        meta["title"] = meta["title_en"]
    # 권 없이 통권 번호만 매기는 학술지(국어교육 170, 독서연구 56 등)는 KCI가 그 번호를
    # <issue>에 싣고 <volume>은 비워 둔다. 참고문헌은 그 번호를 권 자리에 쓰므로
    # (문편협 예시 'Advances in Consumer Research, 13, 208-212.') 원고가 호 없이 권만
    # 적었으면 KCI의 호를 권으로 옮겨 대조한다 — '호 (없음)→170' 오제안 방지(2026-09-11 실측)
    if meta.get("issue") and not meta.get("volume") and not (entry.get("issue") or "").strip():
        meta["volume"], meta["issue"] = meta["issue"], ""
    return meta


# ================================================================ 부가 검사

def _check_retraction(crossref_meta: dict) -> dict | None:
    """Crossref updated-by 필드로 철회·정정·우려표명 확인."""
    updates = crossref_meta.get("updated-by") or []
    if not updates:
        return None
    picked = None
    for u in updates:
        utype = (u.get("type") or "").lower().replace("-", "_")
        if utype in _SEVERE_UPDATES:
            picked = (utype, u)
            break
        if picked is None and utype in _UPDATE_LABEL:
            picked = (utype, u)
    if not picked:
        return None
    utype, u = picked
    date = ""
    parts = (u.get("updated") or {}).get("date-parts") or []
    if parts and parts[0] and parts[0][0]:
        date = "-".join(str(x) for x in parts[0])
    return {"type": utype, "label": _UPDATE_LABEL.get(utype, utype),
            "severe": utype in _SEVERE_UPDATES, "date": date}


_DOAJ_CACHE: dict[str, bool | None] = {}


def _journal_reliability(client: httpx.Client, entry: dict,
                         kci_registration: str = "") -> dict | None:
    """학술지 신뢰성: KCI(국내) / DOAJ·OpenAlex(해외) 대조."""
    if entry.get("type") != "journal":
        return None
    jname = (entry.get("container") or "").strip()
    if not jname or len(jname) < 3:
        return None

    if entry.get("lang") == "ko":
        # KCI가 논문 상세로 알려준 등재 구분이 있으면 그대로 쓴다(학술지명 유사도 추정보다 정확)
        if kci_registration:
            flag = "ok" if "등재" in kci_registration and "후보" not in kci_registration else "warn"
            return {"flag": flag, "detail": f"KCI {kci_registration} 학술지"}
        st = verify_kr.kci_journal_status(client, jname)
        if st == "listed":
            return {"flag": "ok", "detail": "KCI 조회 확인 학술지"}
        if st == "unlisted":
            return {"flag": "warn", "detail": "KCI에서 학술지명 미확인 — 등재 여부 확인 권장"}
        # KCI journalSearch는 미승인이라 대개 여기까지 온다 — RISS 학술지 레코드의
        # 등재정보(KCI등재·우수등재·후보·SCOPUS)·ISSN으로 대신 확인한다
        rs = verify_kr.riss_journal_status(client, jname)
        if rs:
            reg = verify_kr.riss_reg_label(rs.get("reg", ""))
            issn = f" · ISSN {rs['issn']}" if rs.get("issn") else ""
            if reg and "등재" in reg and "후보" not in reg:
                return {"flag": "ok", "detail": f"KCI {reg} 학술지(RISS 확인){issn}"}
            if reg:
                return {"flag": "warn", "detail": f"KCI {reg} 학술지(RISS 확인){issn} — 등재 여부 확인 권장"}
            return {"flag": "warn",
                    "detail": f"RISS 수록 학술지이나 KCI 등재정보 없음{issn} — 등재 여부 확인 권장"}
        if rs == {}:
            return {"flag": "warn", "detail": "RISS에서 학술지명 미확인 — 학술지명·등재 여부 확인 권장"}
        return None  # KCI·RISS 키 없음/조회 불가

    key = jname.lower()
    with _CACHE_LOCK:
        cached = _DOAJ_CACHE.get(key, "miss")
    if cached == "miss":
        found = None
        try:
            r = _get_with_retry(client,
                                "https://doaj.org/api/search/journals/" + quote(jname, safe=""))
            if r.status_code == 200:
                results = r.json().get("results") or []
                found = any(_similarity(jname, ((it.get("bibjson") or {}).get("title") or "")) >= 0.9
                            for it in results)
        except (LookupUnavailable, ValueError):
            found = None
        if found is not None:  # 조회 실패(None)는 캐시하지 않음 — 다음 기회에 재시도
            with _CACHE_LOCK:
                _DOAJ_CACHE[key] = found
        cached = found
    if cached:
        return {"flag": "ok", "detail": "DOAJ 등재 오픈액세스 학술지"}
    return None  # DOAJ 미등재는 정상 구독지도 많으므로 경고하지 않음


def _check_preprint(client: httpx.Client, entry: dict, crossref_meta: dict | None) -> dict | None:
    """프리프린트 인용 식별 + 정식 출판본 제안."""
    doi = (entry.get("doi") or "").lower()
    url = (entry.get("url") or "").lower()
    prefix = doi.split("/")[0] if doi else ""
    server = _PREPRINT_DOI_PREFIX.get(prefix)
    if not server:
        if "arxiv.org" in url:
            server = "arXiv"
        elif "ssrn.com" in url:
            server = "SSRN"
        elif "biorxiv.org" in url or "medrxiv.org" in url:
            server = "bioRxiv/medRxiv"
    if not server:
        # Crossref subtype으로도 식별
        if crossref_meta and crossref_meta.get("subtype") == "preprint":
            server = "프리프린트"
        else:
            return None

    published_doi = ""
    # 1) Crossref relation
    if crossref_meta:
        rel = (crossref_meta.get("relation") or {}).get("is-preprint-of") or []
        for r in rel:
            if r.get("id-type") == "doi":
                published_doi = r.get("id", "")
                break
    # 2) OpenAlex 병합 레코드(정식 출판본 DOI가 canonical로 잡힘)
    if not published_doi and doi:
        try:
            w = _openalex_by_doi(client, doi)
        except LookupUnavailable:
            w = None
        if w:
            canon = (w.get("doi") or "").replace("https://doi.org/", "").lower()
            if canon and canon != doi:
                published_doi = canon
    detail = f"{server} 프리프린트 인용"
    if published_doi:
        detail += f" — 정식 출판본 존재: https://doi.org/{published_doi} 로 교체 권장"
    else:
        detail += " — 정식 출판본 발행 여부 확인 권장"
    return {"published_doi": published_doi, "detail": detail}


def _enrich_from_crossref(client: httpx.Client, result: dict, doi: str):
    """폴백 소스(OpenAlex·S2)에서 DOI를 얻은 경우 Crossref로 정규 서지·철회 여부 보강."""
    if not doi:
        return
    try:
        meta = _crossref_by_doi(client, doi)
    except LookupUnavailable:
        return
    if not meta:
        return
    if not result.get("meta"):
        result["meta"] = _meta_from_crossref(meta)
    if result.get("retraction") is None:
        result["retraction"] = _check_retraction(meta)
        if result["retraction"]:
            lab = result["retraction"]["label"]
            d = result["retraction"]["date"]
            result["detail"] += f" · ⚠ {lab}{'(' + d + ')' if d else ''} 문헌"


def _check_url(client: httpx.Client, url: str) -> tuple[str, str]:
    try:
        r = client.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
        if r.status_code < 400:
            return "ok", f"링크 정상(HTTP {r.status_code})"
        return "dead", f"링크 오류(HTTP {r.status_code}) — 확인 필요"
    except Exception:  # InvalidURL 등 httpx.HTTPError 이외 예외 포함
        return "dead", "링크에 접속할 수 없음 — 확인 필요"


def _safe(fn, *args, **kw):
    """(결과, 일시오류여부) — 일시 오류를 '미발견'과 구분."""
    try:
        return fn(*args, **kw), False
    except LookupUnavailable:
        return None, True


# ================================================================ 본 검증

def _mark_lookup_failed(result: dict):
    result.update(status="skipped",
                  detail="외부 DB 일시 오류(재시도 제한) — 잠시 후 다시 검증해 주세요")


def _crossref_authors_en(meta: dict) -> list[str]:
    """Crossref에 등록된 로마자 저자 표기 — 대조 정보일 뿐 전거가 아니다.

    2026-09-07 실측: 국내 논문 8건 중 5건에서 Crossref 쪽이 발행본 PDF와 일치하고
    KCI 등록 표기가 어긋났다(변우열 Woo-Yeoul/Woo-Yeol, 이병기 Byeong-Ki/Byeong-Kee 등).
    그러나 Crossref도 틀릴 수 있다 — 박주현·변우열(2018)은 발행본 PDF가 Woo-Yeoul인데
    Crossref 등록은 Woo-Yeol이었다(이용자 원문 확인). 국내 학술지는 한글 이름으로
    등록하거나 저자 일부만 올린 레코드도 있다(강봉숙·박주현 2019 한글, 박주현·허우정
    2019 1명만). 따라서 어느 DB도 단독 근거로 쓰지 않고, 발행본을 본 이용자가 확정한
    표기를 전거로 축적한다(authority 모듈).
    """
    out = []
    for a in (meta.get("author") or [])[:30]:
        fam = (a.get("family") or "").strip()
        giv = (a.get("given") or "").strip()
        if not fam or not re.fullmatch(r"[A-Za-z][A-Za-z\-'. ]*", fam):
            return []  # 한글 등록·비로마자면 대조 근거로 쓰지 않는다
        out.append(f"{fam}, {giv}" if giv else fam)
    return out


def _kci_author_note(entry: dict, kci: dict) -> str:
    """원고의 영문 저자 표기가 KCI 등록 표기와 '철자 수준'에서 다르면 안내 문구.

    저자명의 전거는 발행본에 인쇄된 표기다(사용자 확정 정책, 2026-09-06). KCI 등록
    표기는 저자가 직접 올린 값이라 발행본과 어긋난 사례가 있으므로(2026-09-07 실측),
    '틀렸다'가 아니라 '출처마다 다르다'로 알리고 판단은 저자에게 맡긴다.
    붙임표·띄어쓰기·대소문자 차이(Chul-Wan ↔ Chul Wan)는 표기 관행이라 침묵한다.
    """
    official = kci.get("authors_en") or []
    mine = entry.get("authors") or []
    if not official or not mine or not re.search(r"[A-Za-z]", mine[0] or ""):
        return ""

    def norm(x: str) -> str:
        return re.sub(r"[^a-z]", "", (x or "").casefold())

    a, b = norm(mine[0]), norm(official[0])
    if a and b and a != b:
        return (f" · 저자 영문 표기가 출처마다 다름(원고 {mine[0]} / KCI 등록 {official[0]})"
                f" — 인용한 논문 발행본(원문)의 표기를 확인해 정하세요")
    return ""


def _kci_fill_detail(client: httpx.Client, kci: dict) -> tuple[str, bool]:
    """KCI 검색 결과에 빠진 DOI·페이지·등재구분을 articleDetail로 보강.

    반환: (등재구분 문자열, 일시오류 여부).
    """
    reg, err = "", False
    if kci.get("kci_id"):
        detail, err = _safe(verify_kr.kci_article_detail, client, kci["kci_id"])
        if detail:
            reg = detail.get("kci_registration", "")
            for f in ("doi", "pages"):
                if detail.get(f) and not kci.get(f):
                    kci[f] = detail[f]
    return reg, err


def _riss_reg_for(kr: dict) -> str:
    """RISS 적중 레코드의 등재정보('KCI등재,SCOPUS') → KCI 등재구분 표기('등재(SCOPUS)')."""
    if kr.get("source") == "RISS":
        return verify_kr.riss_reg_label(kr.get("reg", ""))
    return ""


def _num_key(v: str) -> str:
    """연도·권·호 비교키 — 숫자가 있으면 숫자만('제28권'과 '28'은 같다), 없으면 구두점 제거."""
    digits = re.sub(r"\D", "", v or "")
    return digits or re.sub(r"[\s\.\-–—()]+", "", (v or "").lower())


def _riss_crosscheck(client: httpx.Client, entry: dict, kr: dict) -> dict | None:
    """KCI 적중 항목을 RISS로 한 번 더 대조 — 두 정보원의 서지 일치 여부(xref).

    RISS(KERIS)는 KCI와 별도로 구축된 서지라, 두 곳이 같으면 판정의 근거가 둘이 되고
    다르면 어느 한쪽의 등록 오류이므로 이용자가 원문을 보게 안내한다. 국내 논문의
    서지 전거는 KCI이므로(사용자 확정, 2026-09-07) 상이해도 status·meta는 바꾸지 않는다.
    RISS 장애·미수록은 KCI 판정을 건드리지 않는다(best-effort — 확인이 안 되면 None).
    검색어는 원고가 아니라 KCI 등록 제목·저자·연도로 넣는다 — 같은 문헌을 찾는 것이
    목적이라 원고 오기의 영향을 받지 않게 한다.
    반환: {"source": "RISS", "state": "agree"|"differ", "url": 레코드 링크, "diff": [str]} | None
    """
    if not verify_kr.riss_enabled():
        return None
    rs, _ = _safe(verify_kr.riss_search, client, kr.get("title") or entry.get("title", ""),
                  (kr.get("authors") or entry.get("authors") or [""])[0],
                  kr.get("year") or entry.get("year", ""), "A")
    if not rs or rs.get("author_mismatch"):
        return None  # 저자가 다른 레코드는 같은 문헌이 아니다 — 일치·상이 어느 쪽도 말하지 않는다
    diff = []
    for f, label in (("year", "연도"), ("volume", "권"), ("issue", "호")):
        a, b = (kr.get(f) or "").strip(), (rs.get(f) or "").strip()
        if a and b and _num_key(a) != _num_key(b):
            diff.append(f"{label} KCI {a}/RISS {b}")
    return {"source": "RISS", "state": "differ" if diff else "agree",
            "url": rs.get("url", ""), "diff": diff}


def _kci_doi_crosscheck(client: httpx.Client, entry: dict, doi: str):
    """해외 DB가 영문 제목만 수록한 국내 논문 방어 — (KCI 레코드|None, 일시오류).

    국내 학술지는 Crossref·OpenAlex에 영문 제목만(혹은 국문 제목만) 올라가는 곳이
    많아, 다른 표기와 대조하면 유사도가 10%대로 떨어져 제대로 쓴 참고문헌이
    '제목 불일치'로 뜬다. 같은 DOI가 KCI에서 확인되면 정상이다(국내 논문은 KCI가
    최종 근거). 영문 인용(로마자 제목)도 KCI가 영문 제목으로 찾아 주므로
    국문 분류에 가두지 않는다(2026-09 실측: 영문 인용 항목이 국문 등록 Crossref와
    어긋나 '제목 불일치'가 됐다).
    """
    if not entry.get("title"):
        return None, False
    kci, err = _safe(verify_kr.kci_article_search, client,
                     entry["title"], (entry.get("authors") or [""])[0])
    if not (kci and kci.get("doi", "").lower() == doi.lower()):
        kci = None
    return kci, err


def verify_entry(client: httpx.Client, entry: dict) -> dict:
    result = _base_result()
    etype = entry.get("type", "")
    lang = entry.get("lang", "ko")
    doi = (entry.get("doi") or "").strip()
    lookup_err = False

    # ---- 1) DOI가 있는 경우: Crossref → DataCite → OpenAlex
    if doi:
        meta, e1 = _safe(_crossref_by_doi, client, doi)
        lookup_err |= e1
        if meta:
            cr_title = " ".join(meta.get("title") or [])
            sim = _best_sim(entry.get("title", ""), meta)
            kci = None
            if sim < 0.75:
                kci, e_kci = _kci_doi_crosscheck(client, entry, doi)
                lookup_err |= e_kci
            if sim >= 0.75 or not entry.get("title"):
                result.update(status="verified", source="Crossref",
                              detail=f"DOI 확인됨 · Crossref 제목 일치({sim:.0%})",
                              meta=_meta_from_crossref(meta))
                # 한국어 논문의 서지 전거는 Crossref보다 KCI가 우선(사용자 확정,
                # 2026-09-07): Crossref 메타는 수록지가 영문 등록명이라 국문 인용의
                # '한국도서관·정보학회지'가 어긋난 것처럼 대조표에 표시된다.
                # 같은 문헌임이 DOI 또는 높은 유사도로 확인될 때만 바꿔 싣는다.
                if lang == "ko" and _HANGUL_RE.search(entry.get("title", "")):
                    kci2, e_k2 = _safe(verify_kr.kci_article_search, client,
                                       entry.get("title", ""),
                                       (entry.get("authors") or [""])[0])
                    lookup_err |= e_k2
                    if kci2 and (kci2.get("doi", "").lower() == doi.lower()
                                 or kci2.get("sim", 0) >= 0.9):
                        result["meta"] = _meta_kr_for_entry(entry, kci2)
                        result["detail"] += " · 서지는 KCI 기준(국문)"
                if lang == "west" and etype == "journal":
                    _eric_print_crosscheck(client, entry, result)
            elif kci:
                my_lang = "국문" if _HANGUL_RE.search(entry.get("title", "")) else "영문"
                cr_lang = "국문" if _HANGUL_RE.search(cr_title) else "영문"
                result.update(status="verified", source="KCI",
                              detail=f"DOI 확인됨 · KCI {my_lang} 제목 일치({kci.get('sim', 0):.0%})"
                                     f" · Crossref에는 {cr_lang} 제목으로 등록됨",
                              meta=_meta_kr_for_entry(entry, kci))
            else:
                result.update(status="mismatch", source="Crossref",
                              detail=f"DOI는 존재하나 제목 불일치({sim:.0%}) — Crossref: “{cr_title[:80]}” · 확인 필요",
                              meta=_meta_from_crossref(meta))
            # 발행본에서 등록된 로마자 저자 표기 — 화면의 '표기 대조' 근거로만 싣는다.
            # 이 블록이 위 if/elif 사이에 끼어 있어(2026.09.07-07) Crossref에 로마자 저자가 없는
            # 국내 논문은 제목이 100% 일치해도 else로 떨어져 '서지 불일치'가 되었다(2026-09-11 신고:
            # 장은섭 2019). 판정 사슬 뒤로 옮긴다.
            cr_au = _crossref_authors_en(meta)
            if cr_au and result.get("meta") is not None:
                result["meta"]["authors_cr"] = cr_au
            result["retraction"] = _check_retraction(meta)
            if result["retraction"]:
                lab = result["retraction"]["label"]
                d = result["retraction"]["date"]
                result["detail"] += f" · ⚠ {lab}{'(' + d + ')' if d else ''} 문헌"
            result["preprint"] = _check_preprint(client, entry, meta)
            result["journal"] = _journal_reliability(client, entry)
            return result
        dc, e2 = _safe(_datacite_by_doi, client, doi)
        lookup_err |= e2
        if dc:
            titles = dc.get("titles") or [{}]
            dc_title = titles[0].get("title", "") if titles else ""
            sim = _similarity(entry.get("title", ""), dc_title)
            if entry.get("title") and dc_title and sim < 0.60:
                kci, e_kci = _kci_doi_crosscheck(client, entry, doi)
                lookup_err |= e_kci
                if kci:
                    result.update(status="verified", source="KCI",
                                  detail=f"DOI 확인됨 · KCI 제목 일치({kci.get('sim', 0):.0%})"
                                         f" · DataCite에는 다른 표기의 제목으로 등록됨",
                                  meta=_meta_kr_for_entry(entry, kci))
                    result["preprint"] = _check_preprint(client, entry, None)
                    return result
                result.update(status="mismatch", source="DataCite",
                              detail=f"DOI는 존재하나(DataCite) 제목 불일치({sim:.0%}) — “{dc_title[:80]}” · 확인 필요")
            else:
                sim_txt = f" · 제목 일치({sim:.0%})" if entry.get("title") and dc_title else ""
                result.update(status="verified", source="DataCite",
                              detail=f"DOI 확인됨(DataCite — 데이터셋/리포지터리류){sim_txt}")
            result["preprint"] = _check_preprint(client, entry, None)
            return result
        w, e3 = _safe(_openalex_by_doi, client, doi)
        lookup_err |= e3
        if w:
            oa_title = w.get("title") or ""
            sim = _similarity(entry.get("title", ""), oa_title)
            if entry.get("title") and oa_title and sim < 0.75:
                kci, e_kci = _kci_doi_crosscheck(client, entry, doi)
                lookup_err |= e_kci
                if kci:
                    result.update(status="verified", source="KCI",
                                  detail=f"DOI 확인됨 · KCI 제목 일치({kci.get('sim', 0):.0%})"
                                         f" · OpenAlex에는 다른 표기의 제목으로 등록됨",
                                  meta=_meta_kr_for_entry(entry, kci))
                    result["preprint"] = _check_preprint(client, entry, None)
                    return result
                result.update(status="mismatch", source="OpenAlex",
                              detail=f"DOI는 존재하나(OpenAlex) 제목 불일치({sim:.0%}) — “{oa_title[:80]}” · 확인 필요")
            else:
                result.update(status="verified", source="OpenAlex",
                              detail=f"DOI 확인됨(OpenAlex) · 제목 일치({sim:.0%})",
                              meta=_meta_from_openalex(w))
            result["preprint"] = _check_preprint(client, entry, None)
            return result
        if lookup_err:
            _mark_lookup_failed(result)
        else:
            result.update(status="not_found",
                          detail="DOI를 Crossref·DataCite·OpenAlex에서 찾을 수 없음 — DOI 오기 가능성, 확인 필요")
        return result

    # ---- 2) 국내 문헌: KCI(논문, 적중 시 RISS 교차 확인) / RISS(학위논문) / 국립중앙도서관(단행본)
    #         못 찾으면 유형별로 RISS · 국회도서관 · Crossref 순으로 폴백
    if lang == "ko" and etype in ("journal", "thesis", "book", "report"):
        kr = None
        xref = None
        title = entry.get("title", "")
        authors = entry.get("authors") or []
        first_author = authors[0] if authors else ""
        year = entry.get("year", "")
        # 국내 DB도 해외와 같은 규약 — 일시 오류는 '미발견'이 아니라 '확인 못 함'으로 모은다
        if etype == "journal":
            kr, e_k = _safe(verify_kr.kci_article_search, client, title, first_author)
            lookup_err |= e_k
            if kr:
                # 국내 논문의 전거는 KCI. RISS는 독립된 제2 정보원으로 같은 문헌을 한 번 더
                # 확인해 두 서지의 일치 여부를 부기한다(장애 시 KCI 판정에 영향 없음)
                xref = _riss_crosscheck(client, entry, kr)
            else:
                # KCI 미등재지·등록 누락 논문 — RISS 국내학술논문(A)으로 한 번 더 본다
                kr, e_r = _safe(verify_kr.riss_search, client, title, first_author, year, "A")
                lookup_err |= e_r
        elif etype == "thesis":
            # RISS(KERIS 학위논문 종합목록)가 수록이 가장 넓고 수여기관·학위 구분까지 준다
            # — 국회도서관은 RISS에 없을 때만 본다
            kr, e_r = _safe(verify_kr.riss_search, client, title, first_author, year, "T")
            lookup_err |= e_r
            if not kr:
                kr, e_k = _safe(verify_kr.nanet_search, client, title, year)
                lookup_err |= e_k
        elif etype in ("book", "report"):
            kr, e_k = _safe(verify_kr.nlk_book_search, client, title, first_author, year)
            lookup_err |= e_k
            if not kr and etype == "book":
                # 시판 도서(해외서 포함)는 카카오 책(Daum 책)이 SEOJI 누락을 메운다 —
                # 전남대 도서관 외부기관검색과 같은 구성(kakao책·RISS·국립중앙·국회)
                kr, e_kk = _safe(verify_kr.kakao_book_search, client, title, first_author, year)
                lookup_err |= e_kk
            if not kr:
                kr, e_k2 = _safe(verify_kr.nanet_search, client, title, year)
                lookup_err |= e_k2
            if not kr:
                # ISBN 없는 기관 발간물·연구보고서는 SEOJI에 없다 — RISS 단행본(U)·연구보고서(F)
                kr, e_r = _safe(verify_kr.riss_search, client, title, first_author, year,
                                verify_kr.RISS_TYPE[etype])
                lookup_err |= e_r
        if kr and kr.get("author_mismatch"):
            # 제목은 같은데 저자가 다른 RISS 레코드 — 동명 서명·동명 학위논문을 다른 사람의
            # 문헌으로 '확인'하고 그 서지로 교정까지 제안하게 두지 않는다
            who = ", ".join(kr.get("authors") or [])[:60]
            result.update(status="mismatch", source=kr.get("source", "RISS"),
                          detail=f"{kr.get('source')}에 같은 제목의 문헌이 있으나 저자가 다름"
                                 f"(원고 {first_author} / {kr.get('source')} {who}) — 확인 필요",
                          meta=None)
            return result
        if kr:
            # KCI 검색 결과에는 DOI·페이지·등재구분이 빠져 있어 상세 조회로 보강한다
            reg, e_d = _kci_fill_detail(client, kr)
            lookup_err |= e_d
            detail = f"{kr.get('source')} 대조 성공(제목 일치 {kr.get('sim', 0):.0%})"
            detail += _kci_author_note(entry, kr)
            if kr.get("mtype"):
                detail += f" · {kr['mtype']}"  # RISS 자료유형 — 국내석사·해외박사(DDOD)·단행본 등
            if kr.get("isbn"):
                # 같은 서명의 다른 판과 헷갈릴 때 이용자가 손으로 확인할 수 있는 유일한 값
                detail += f" · ISBN {kr['isbn']}"
            if kr.get("note"):
                detail += f" · {kr['note']}"   # 카카오 책: 다른 판만 수록 등 판정 단서
            if xref:
                detail += (" · RISS 교차 확인 일치" if xref["state"] == "agree" else
                           " · RISS 교차 확인: 서지 상이(" + ", ".join(xref["diff"])
                           + ") — 원문에서 확인 권장")
            result.update(status="verified", source=kr.get("source", "국내DB"),
                          detail=detail, meta=_meta_kr_for_entry(entry, kr), xref=xref)
            if kr.get("doi"):
                result["found_doi"] = kr["doi"]
                _enrich_from_crossref(client, result, kr["doi"])  # 철회 여부 보강
            if etype == "journal":
                if kr.get("source") == "RISS":
                    # RISS 적중 — 등재정보는 RISS 학술지 레코드(등재구분·ISSN)로 확인하고,
                    # 그마저 안 되면 논문 레코드의 등재정보를 'RISS 확인'으로 표기한다
                    result["journal"] = _journal_reliability(client, entry)
                    if not result["journal"] and (reg := _riss_reg_for(kr)):
                        result["journal"] = {"flag": "ok" if "등재" in reg and "후보" not in reg else "warn",
                                             "detail": f"KCI {reg} 학술지(RISS 확인)"}
                else:
                    result["journal"] = _journal_reliability(client, entry, reg)
            return result
        # 국내 학술지 논문은 Crossref에도 상당수 등재 — 이어서 시도
        if etype == "journal":
            best, e_kr = _safe(_crossref_search, client, entry)
            lookup_err |= e_kr
            if best:
                result.update(status="verified", source="Crossref",
                              detail=f"Crossref 대조 성공(제목 일치 {best.get('_sim', 0):.0%})",
                              found_doi=best.get("DOI", ""), meta=_meta_from_crossref(best))
                result["retraction"] = _check_retraction(best)
                if result["retraction"]:
                    lab = result["retraction"]["label"]
                    d = result["retraction"]["date"]
                    result["detail"] += f" · ⚠ {lab}{'(' + d + ')' if d else ''} 문헌"
                result["journal"] = _journal_reliability(client, entry)
                return result
        st = verify_kr.kr_api_status()
        # 실제로 대조한 정보원만 문구에 적는다 — 키가 없는 DB를 '찾아봤다'고 하지 않는다
        tried = {"journal": [("KCI", st["kci"]), ("RISS", st["riss"])],
                 "thesis": [("RISS", st["riss"]), ("국회도서관", st["nanet"])],
                 "book": [("국립중앙도서관", st["nlk"]), ("카카오 책", st["kakao"]),
                          ("국회도서관", st["nanet"]), ("RISS", st["riss"])],
                 "report": [("국립중앙도서관", st["nlk"]), ("국회도서관", st["nanet"]), ("RISS", st["riss"])],
                 }.get(etype, [])
        used = [name for name, on in tried if on]
        if used and lookup_err:
            # 조회 자체가 실패한 경우 — '없는 문헌'으로 오해하게 두지 않는다
            _mark_lookup_failed(result)
        elif used:
            dbs = "·".join(used) + ("·Crossref" if etype == "journal" else "")
            detail = f"{dbs}에서 일치 문헌을 찾지 못함 — 서지사항 확인 필요"
            if etype in ("book", "report"):
                # 국립중앙도서관 서지정보는 ISBN이 붙은 도서만 담고 있어, 비매품
                # 기관 발간물·정부간행물은 실제로 존재해도 걸리지 않는다. RISS 단행본
                # 종합목록이 일부를 보완하지만 수록에 구멍이 있다. 이를 알려 주지 않으면
                # '없는 문헌'으로 오해해 멀쩡한 참고문헌을 지우게 된다.
                if st["nlk"] and st["riss"]:
                    detail += (" (ISBN 없는 비매품·기관 발간물은 국립중앙도서관 서지에 없고 RISS 수록도"
                               " 일부라, 실제로 존재해도 여기서는 확인되지 않을 수 있습니다)")
                elif st["riss"]:
                    detail += (" (RISS 단행본 종합목록은 수록에 구멍이 있어, 실제로 존재해도"
                               " 여기서는 확인되지 않을 수 있습니다)")
                else:
                    detail += (" (ISBN 없는 비매품·기관 발간물은 국내 DB에 수록되지 않아, 실제로 존재해도"
                               " 여기서는 확인되지 않습니다)")
            result.update(status="not_found", detail=detail)
        else:
            result.update(status="skipped",
                          detail="국내 문헌 — 국내 DB 검증용 API 키 미설정(관리자 설정 참조), KCI·RISS에서 확인 권장")
        if entry.get("url"):
            stt, det = _check_url(client, entry["url"])
            result["detail"] += f" · {det}"
        return result

    # ---- 3) 해외 학술지 논문: Crossref → (연도 무시 재검색) → OpenAlex → Semantic Scholar
    if etype == "journal":
        best, e1 = _safe(_crossref_search, client, entry)
        lookup_err |= e1
        if not best and not e1:
            # 연도 오기 가능성: 연도 조건 없이 고유사도 재검색(교정 제안으로 이어짐)
            best, e1b = _safe(_crossref_search, client, entry, ignore_year=True, threshold=0.93)
            lookup_err |= e1b
        if best:
            result.update(status="verified", source="Crossref",
                          detail=f"Crossref 대조 성공(제목 일치 {best.get('_sim', 0):.0%})"
                                 + (f" · DOI 발견: {best.get('DOI', '')}" if best.get("DOI") else ""),
                          found_doi=best.get("DOI", ""), meta=_meta_from_crossref(best))
            result["retraction"] = _check_retraction(best)
            if result["retraction"]:
                lab = result["retraction"]["label"]
                d = result["retraction"]["date"]
                result["detail"] += f" · ⚠ {lab}{'(' + d + ')' if d else ''} 문헌"
            result["journal"] = _journal_reliability(client, entry)
            return result
        w, e2 = _safe(_openalex_search, client, entry)
        lookup_err |= e2
        if w:
            oa_doi = (w.get("doi") or "").replace("https://doi.org/", "")
            result.update(status="verified", source="OpenAlex",
                          detail=f"OpenAlex 대조 성공(제목 일치 {w.get('_sim', 0):.0%})"
                                 + (f" · DOI 발견: {oa_doi}" if oa_doi else ""),
                          found_doi=oa_doi, meta=_meta_from_openalex(w))
            _enrich_from_crossref(client, result, oa_doi)
            result["journal"] = _journal_reliability(client, entry)
            return result
        s2, e3 = _safe(_s2_match, client, entry)
        lookup_err |= e3
        if s2:
            s2_doi = ((s2.get("externalIds") or {}).get("DOI") or "")
            result.update(status="verified", source="Semantic Scholar",
                          detail=f"Semantic Scholar 대조 성공(제목 일치 {s2.get('_sim', 0):.0%})"
                                 + (f" · DOI 발견: {s2_doi}" if s2_doi else ""),
                          found_doi=s2_doi)
            _enrich_from_crossref(client, result, s2_doi)
            return result
        # 교육학 오픈액세스 학술지(School Library Research 등)는 DOI가 없어 위 3개
        # DB에 색인되지 않는다 — 실존 의심 전에 ERIC으로 한 번 더 확인
        er, e_er = _safe(_eric_search, client, entry)
        lookup_err |= e_er
        if er:
            result.update(status="verified", source="ERIC",
                          detail=f"ERIC 대조 성공(제목 일치 {er.get('_sim', 0):.0%})"
                                 + (f" · ERIC 문헌번호 {er['id']}" if er.get("id") else ""),
                          meta=_meta_from_eric(er))
            return result
        # 국내 논문의 영문 인용: 해외 DB에 없어도 KCI에는 저자가 등록한 공식 영문
        # 제목이 있다(2026-09 실측: 곽철완 2006 영문 인용이 KCI 1건 적중). 실존
        # 의심으로 판정하기 전에 KCI를 영문 제목으로 대조한다.
        kci_used = verify_kr.kr_api_status()["kci"]
        kci, e4 = _safe(verify_kr.kci_article_search, client, entry.get("title", ""),
                        (entry.get("authors") or [""])[0])
        lookup_err |= e4
        if kci:
            reg, e_d = _kci_fill_detail(client, kci)
            lookup_err |= e_d
            result.update(status="verified", source="KCI",
                          detail=f"KCI 대조 성공(제목 일치 {kci.get('sim', 0):.0%}) — "
                                 f"국내 학술지 논문의 영문 인용"
                                 + _kci_author_note(entry, kci),
                          meta=_meta_kr_for_entry(entry, kci))
            if kci.get("doi"):
                result["found_doi"] = kci["doi"]
                _enrich_from_crossref(client, result, kci["doi"])  # 철회 여부 보강
            if reg:
                result["journal"] = {"flag": "ok" if "등재" in reg and "후보" not in reg else "warn",
                                     "detail": f"KCI {reg} 학술지"}
            return result
        # KCI 미등재 국내 학술지의 영문 인용은 RISS 레코드에 병기된 영문 제목으로 잡힌다
        # (2026-09 실측: '(Disaster Prevention Education …)' 병기 제목이 영문 검색에 적중)
        riss_used = verify_kr.riss_enabled()
        rs, e5 = _safe(verify_kr.riss_search, client, entry.get("title", ""),
                       (entry.get("authors") or [""])[0], entry.get("year", ""), "A")
        lookup_err |= e5
        if rs:
            result.update(status="verified", source="RISS",
                          detail=f"RISS 대조 성공(제목 일치 {rs.get('sim', 0):.0%}) — "
                                 f"국내 학술지 논문의 영문 인용",
                          meta=_meta_kr_for_entry(entry, rs))
            reg = _riss_reg_for(rs)
            if reg:
                result["journal"] = {"flag": "ok" if "등재" in reg and "후보" not in reg else "warn",
                                     "detail": f"KCI {reg} 학술지(RISS 확인)"}
            return result
        if lookup_err:
            _mark_lookup_failed(result)  # 일시 오류를 '실존 의심'으로 오판하지 않음
        elif entry.get("lang") == "west":
            dbs = ("Crossref·OpenAlex·Semantic Scholar·ERIC" + ("·KCI" if kci_used else "")
                   + ("·RISS" if riss_used else ""))
            result.update(status="suspect",
                          detail=f"{dbs} 모두 미발견 — "
                                 "실존 의심(AI 생성 인용·서지 오류 가능성), 반드시 확인 필요")
        else:
            result.update(status="skipped", detail="다중 DB 미발견 — 원문 DB에서 확인 권장")
        return result

    # ---- 3b) 해외 학위논문: RISS 학위논문 API가 ProQuest 해외 박사논문(DDOD)까지 수록한다
    #          (2026-09 실측) — 종전에는 '검증 대상 아님'으로 건너뛰던 유형
    #          동양 문헌(lang=east)은 RISS 해외 학위논문 수록 대상이 아니라 종전대로 둔다
    if etype == "thesis" and lang == "west" and verify_kr.riss_enabled():
        rs, e_t = _safe(verify_kr.riss_search, client, entry.get("title", ""),
                        (entry.get("authors") or [""])[0], entry.get("year", ""), "T")
        lookup_err |= e_t
        if rs and rs.get("author_mismatch"):
            who = ", ".join(rs.get("authors") or [])[:60]
            result.update(status="mismatch", source="RISS",
                          detail=f"RISS에 같은 제목의 학위논문이 있으나 저자가 다름"
                                 f"(원고 {(entry.get('authors') or [''])[0]} / RISS {who}) — 확인 필요")
            return result
        if rs:
            result.update(status="verified", source="RISS",
                          detail=f"RISS 대조 성공(제목 일치 {rs.get('sim', 0):.0%})"
                                 + (f" · {rs['mtype']}" if rs.get("mtype") else ""),
                          meta=_meta_kr_for_entry(entry, rs))
            return result
        if not verify_kr.riss_enabled():
            lookup_err = True  # 조회 도중 다른 스레드가 인증 오류로 RISS를 껐다 — '미발견'이 아니다
        # 국내 대학이 구입한 해외 박사논문만 실려 있어 미발견이 곧 허위는 아니다
        note = ("RISS(해외 학위논문)에서 일치 문헌을 찾지 못함 — 수록 범위가 제한적이라 "
                "실제로 존재해도 확인되지 않을 수 있습니다, 원문 확인 권장")
        if entry.get("url"):
            # URL이 있는 해외 학위논문은 종전처럼 링크 생존으로 판정한다(링크가 살아 있으면
            # '문제' 항목으로 세지 않는다) — RISS 미수록은 부기만 한다
            stt, det = _check_url(client, entry["url"])
            result.update(status="link_ok" if stt == "ok" else "link_dead",
                          detail=f"{det} · " + ("RISS 조회 실패(일시 오류)" if lookup_err else note))
            return result
        if lookup_err:
            _mark_lookup_failed(result)
        else:
            result.update(status="not_found", detail=note)
        return result

    # ---- 3-2) 법령 — 국가법령정보센터(법제처)에서 실존·현행 공포번호·영문 법령명 대조
    #      (사용자 요청 2026-09-11). 공포번호가 현행과 다르면 갱신 검토 제안(_build_suggestions).
    if etype == "law" and _HANGUL_RE.search(entry.get("title", "")):
        law, e_law = _safe(verify_kr.law_search, client, entry.get("title", ""))
        lookup_err |= e_law
        if law:
            my_no = _law_no_digits(entry.get("report_no") or entry.get("raw") or "")
            detail = (f"국가법령정보센터 확인 · 현행 {law['no_label']}"
                      f"({verify_kr._law_date(law['date'])} {law['amend']}, "
                      f"시행 {verify_kr._law_date(law['eff'])})")
            if law.get("name_en"):
                detail += f" · 영문 법령명(법제처 영어번역) {law['name_en']}"
            if my_no and law.get("no") and my_no != law["no"]:
                detail += (f" · 원고의 제{my_no}호는 현행 공포번호와 다름 — 이전 개정본을 인용한 "
                           "것이면 그대로, 현행법을 뜻한다면 갱신")
            if law.get("by_abbr"):
                detail += f" · 원고의 '{entry.get('title', '').strip()}'은 약칭 — 정식 명칭은 '{law['name']}'"
            result.update(status="verified", source="국가법령정보센터", detail=detail,
                          meta={"source": "국가법령정보센터", "title": law["name"],
                                "title_en": law.get("name_en", ""), "report_no": law["no_label"],
                                "law_url": law["url"], "law_en_url": law["en_url"],
                                "law_kind": law["kind"], "law_date": verify_kr._law_date(law["date"]),
                                "law_eff": verify_kr._law_date(law["eff"])})
            return result
        if lookup_err:
            _mark_lookup_failed(result)
        else:
            result.update(status="not_found", source="국가법령정보센터",
                          detail="국가법령정보센터에서 같은 이름의 법령을 찾지 못함 — 약칭·옛 명칭이면 "
                                 "정식 명칭으로(예: 학교도서관법 → 학교도서관진흥법), 시행령·시행규칙은 "
                                 "그 이름까지 적었는지 확인")
        return result

    # ---- 3-b) 해외 단행본: 카카오 책(국내 유통 해외서) — 지금까지는 '오프라인 자료'로 전부
    #      생략했다. 수록이 부분적이라 못 찾아도 '미발견'이 아니라 생략(skipped)으로 둔다.
    if etype == "book" and verify_kr.kr_api_status().get("kakao"):
        kr, e_kk = _safe(verify_kr.kakao_book_search, client, entry.get("title", ""),
                         (entry.get("authors") or [""])[0], entry.get("year", ""))
        lookup_err |= e_kk
        if kr and not kr.get("author_mismatch"):
            detail = f"카카오 책 대조 성공(제목 일치 {kr.get('sim', 0):.0%})"
            if kr.get("isbn"):
                detail += f" · ISBN {kr['isbn']}"
            if kr.get("note"):
                detail += f" · {kr['note']}"
            result.update(status="verified", source="카카오 책", detail=detail, meta=_meta_from_kr(kr))
            return result
        result.update(detail="검증 대상 아님(오프라인 자료) — 카카오 책(국내 유통 해외서)에서도 찾지 못함"
                             if not lookup_err else "검증 대상 아님(오프라인 자료) — 카카오 책 조회 실패")
        if kr and kr.get("author_mismatch"):
            who = ", ".join(kr.get("authors") or [])[:60]
            result["detail"] += f" · 같은 제목의 다른 저자 도서만 있음({who})"
        return result

    # ---- 4) URL만 있는 자료
    url = (entry.get("url") or "").strip()
    if url:
        st, detail = _check_url(client, url)
        result.update(status="link_ok" if st == "ok" else "link_dead", detail=detail)
        return result

    result.update(detail="검증 대상 아님(오프라인 자료)")
    return result


def _law_no_digits(s: str) -> str:
    """'법률 제21447호' / 'Act No. 21447' → '21447'."""
    m = re.search(r"제\s*(\d+)\s*호|No\.?\s*(\d+)", s or "")
    return (m.group(1) or m.group(2)) if m else ""


def verify_entries(entries: list[dict], progress_cb=None) -> list[dict]:
    results: list[dict | None] = [None] * len(entries)
    with httpx.Client() as client:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(verify_entry, client, e): i for i, e in enumerate(entries)}
            done = 0
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    results[i] = fut.result()
                except Exception as ex:
                    r = _base_result()
                    r["detail"] = f"검증 중 오류: {ex}"
                    results[i] = r
                done += 1
                if progress_cb:
                    progress_cb(done, len(entries))
    return [r or _base_result() for r in results]
