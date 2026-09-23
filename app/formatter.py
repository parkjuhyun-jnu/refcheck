# -*- coding: utf-8 -*-
"""문편협 공통기준(2024. 6. 17. 개정) 형식 변환·정렬·형식 검증."""
import re

# 원고가 소절 표제로 명시한 '국한문 참고문헌의 영문 표기' 항목의 그룹 표제.
# main(그룹핑)·hwpx_export·report(목록 내보내기)가 같은 문자열을 본다.
GROUP_LABEL_CONV = "국문 문헌의 영문 변환 표기"
_CJK_RE = re.compile(r"[가-힣一-鿿぀-ゟ゠-ヿ]")

# ---------------------------------------------------------------- 저자 표기

_SMALL_WORDS = {"a", "an", "the", "and", "or", "of", "in", "on", "for", "to",
                "with", "at", "by", "from", "as", "but", "nor", "vs"}


# 단체·기관 저자를 알아보는 단서. 공통기준 Ⅰ-6)은 단체명을 그대로 기재하도록 하며
# (예: Public Library Association), 인명처럼 뒤집으면 'IFLA Study Group on the FRBR'이
# 'FRBR, I. S. G. O. T.'가 되어 버린다.
_ORG_FUNCTION_WORDS = {"of", "on", "the", "for", "and", "in", "at", "&"}
_ORG_WORDS = {
    "association", "society", "institute", "institution", "university", "college",
    "school", "library", "libraries", "group", "committee", "council", "department",
    "division", "ministry", "agency", "bureau", "center", "centre", "foundation",
    "organization", "organisation", "board", "commission", "office", "museum",
    "archives", "federation", "union", "corporation", "company", "press",
    "national", "international", "federal", "administration", "network",
    "consortium", "academy", "authority", "service", "services", "project",
    # 'Softlink Education'을 인명으로 보고 'Education, S.'로 뒤집던 문제(2026-09 실측)
    "education", "research", "publishing", "publishers", "media", "solutions",
}


def _is_org_name(name: str) -> bool:
    """단체·기관 저자인지 — 인명 뒤집기를 건너뛸지 판단한다."""
    words = [w for w in re.split(r"[\s,]+", name.strip()) if w]
    if len(words) < 2:
        return False  # 한 낱말은 원래 뒤집지 않는다(IFLA 등)
    low = {w.lower().strip(".") for w in words}
    # 인명에는 들어가지 않는 기능어(of·on·the…)나 기관 명칭어가 있으면 단체로 본다
    return bool(low & _ORG_FUNCTION_WORDS) or bool(low & _ORG_WORDS)


# 로마자 표기 한국 성씨(주요 이형 포함) — 공통기준 Ⅱ-1)(3)은 '국내를 포함한 중국,
# 일본 저자는 성명을 그대로 기재'하고 서양 인명만 이름을 두문자로 줄이게 한다.
# 국문 문헌의 영문 인용(Byun, Woo-Yeoul)을 서양 저자로 보고 'Byun, W. Y.'로
# 줄이던 문제의 방어선(2026-09 실측).
# 이 목록은 서양문헌 속 한국인 공저자를 알아보는 보조 단서일 뿐이다 — 빠진 성씨가 나오면
# 이름이 두문자로 줄어든다(2026-09-11 실측: '천'(Cheon)이 빠져 'Cheon, Gyeongrok'이
# 'Cheon, G.'로). 그래서 국문 문헌의 영문 변환 항목(is_en_conversion)과 국내문헌은
# 이 목록과 무관하게 format_authors에서 항상 전체 이름을 유지한다.
_KR_SURNAMES = {
    "kim", "gim", "lee", "yi", "rhee", "ri", "park", "pak", "bak", "choi", "choe",
    "jung", "jeong", "chung", "cheong", "kang", "gang", "cho", "jo", "yoon", "yun", "youn",
    "jang", "chang", "lim", "im", "rim", "yim", "han", "oh", "seo", "suh", "shin", "sin",
    "kwon", "gwon", "kweon", "hwang", "whang", "ahn", "an", "song", "yoo", "yu", "you",
    "ryu", "ryoo", "lyu", "hong", "jeon", "chun", "jun", "chon", "ko", "koh", "go",
    "moon", "mun", "yang", "ryang", "son", "sohn", "bae", "pae", "baek", "paik", "baik",
    "back", "heo", "hur", "huh", "her", "noh", "roh", "no", "ro", "nam", "sim", "shim",
    "ha", "joo", "ju", "chu", "choo", "koo", "gu", "ku", "goo", "min", "byun", "byeon",
    "byon", "pyun", "kwak", "gwak", "kwag", "sung", "seong", "cha", "woo", "wu", "kil",
    "gil", "hyun", "hyeon", "hu", "na", "ra", "nah", "la", "do", "doh", "seok", "suk",
    "sok", "pyo", "chae", "won", "jin", "chin", "ok", "maeng", "bang", "pang", "pyeon",
    "pyon", "byeong", "myung", "myeong", "cheon", "cheun", "kong", "gong", "ham", "yeom",
    "yum", "youm", "yeo", "yuh", "so", "soh", "sun", "seon", "seol", "sul", "ma", "mah",
    "yeon", "wi", "wee", "ki", "gi", "kee", "ban", "wang", "keum", "geum", "kum", "yook",
    "yuk", "in", "ihn", "je", "jae", "mo", "moh", "tak", "kook", "guk", "kuk", "eom",
    "um", "uhm", "om", "eo", "uh", "eun", "un", "yong", "ye", "bong", "sa", "bu", "boo",
    "pi", "gam", "kam", "tae", "gal", "kal", "kyung", "gyeong", "kyoung", "bin", "jong",
    "seung", "si", "shi", "mok", "ji", "jee", "chi", "hwa", "sang", "namgung", "namkoong",
    "hwangbo", "sunwoo", "seonu", "jegal", "sagong", "seomun", "dokgo", "dongbang",
}


def _east_asian_full_name(last: str, first: str) -> bool:
    """로마자 표기 동아시아 저자로 보이면 True — 이름을 두문자로 줄이지 않는다."""
    toks = [t for t in re.split(r"[\s\-]+", first) if t]
    if not toks or any(len(t.rstrip(".")) <= 1 for t in toks):
        return False  # 'J. A.'처럼 이미 두문자면 서양식 표기
    if len(toks) == 2 and ("-" in first or all(2 <= len(t) <= 7 for t in toks)):
        return True   # Woo-Yeoul · Bong-suk · Jee Yeon · Bo Seong 꼴
    return last.lower().rstrip(".") in _KR_SURNAMES  # Park, Juhyeon / Hong, Soram 꼴


def _west_author(name: str, keep_full: bool = False) -> str:
    """서양 저자명 → 'Last, F. M.' 형식. 단체·기관명은 그대로 둔다.

    keep_full=True면 이름을 두문자로 줄이지 않고 'Last, First'로 둔다 — 국문 문헌의
    영문 변환 항목처럼 저자가 한국인임이 확정된 경우(공통기준 Ⅱ-1)(3): 국내 저자는
    성명을 그대로).
    """
    name = name.strip().rstrip(".")
    if not name:
        return name
    if _is_org_name(name):
        return name
    if "," in name:  # 이미 Last, First 형태
        last, first = [p.strip() for p in name.split(",", 1)]
    else:
        parts = name.split()
        if len(parts) == 1:
            return name
        last, first = parts[-1], " ".join(parts[:-1])
    if (keep_full and not _is_initials(first)) or _east_asian_full_name(last, first):
        return f"{last}, {first}" if first else last
    initials = " ".join(
        f"{w[0].upper()}." for w in re.split(r"[\s\.\-]+", first) if w and w[0].isalpha()
    )
    return f"{last}, {initials}" if initials else last


def _is_initials(first: str) -> bool:
    """'S.', 'S. S', 'M.C.'처럼 이미 두문자인 이름 — 전체 이름으로 되돌릴 수 없으니 두문자 표기로 정리."""
    toks = [t for t in re.split(r"[\s\.\-]+", first or "") if t]
    return bool(toks) and all(len(t) == 1 for t in toks)


def has_initials(authors: list[str]) -> bool:
    """저자 목록에 두문자로 줄어든 이름이 있는가('Yang, S.' / 'Park, S. S.')."""
    for a in authors or []:
        if "," in a and _is_initials(a.split(",", 1)[1].strip()):
            return True
    return False


_EN_AUTHOR_HEAD_RE = re.compile(r"^(.*?)\s\((\d{4}[a-z]?|n\.d\.)\)")


def authors_from_en_raw(raw: str) -> list[str]:
    """영문 변환 항목 원문의 저자부('Yang, Sooyeon, Park, Seong Seog, & Min, Byeonggon (2020)…')
    → ['Yang, Sooyeon', 'Park, Seong Seog', 'Min, Byeonggon']. 저자부를 못 찾으면 []."""
    m = _EN_AUTHOR_HEAD_RE.match(re.sub(r"\s+", " ", raw or "").strip())
    if not m:
        return []
    seg = m.group(1).strip()
    if not seg or _is_org_name(seg) and "," not in seg:
        return []
    pieces = [p.strip() for p in re.split(r",\s*&\s*|\s&\s|;\s*", seg) if p.strip()]
    out: list[str] = []
    for piece in pieces:
        toks = [t.strip() for t in piece.split(",") if t.strip()]
        if len(toks) >= 2 and len(toks) % 2 == 0:
            out.extend(f"{toks[i]}, {toks[i + 1]}" for i in range(0, len(toks), 2))
        else:
            out.append(piece)
    return out


def format_authors(entry: dict) -> str:
    authors = [a for a in entry.get("authors", []) if a and a.strip()]
    note = entry.get("author_note", "").strip()
    lang = entry.get("lang", "ko")
    if not authors:
        return ""
    # 로마자 저자 목록은 국내문헌으로 분류돼 있어도 영문 표기 규칙을 따른다 —
    # 공통기준 Ⅱ-1)(4): 서양(로마자) 저자는 2인이어도 마지막 저자 앞에 앤드기호(&).
    # (예: 'Kang, Bong-suk & Park, Juhyeon'. ko 분류 시 &가 지워지던 문제 — 2026-09 실측)
    roman = all(re.search(r"[A-Za-z]", a) and not re.search(r"[가-힣一-鿿぀-ゟ゠-ヿ]", a)
                for a in authors)
    if lang == "west" or roman:
        # 국문 문헌의 영문 변환 항목(소절 표제로 확정)과 국내문헌의 로마자 저자는 한국인이다 —
        # 성씨 목록에 없어도 두문자로 줄이지 않는다(영문 변환 목록의 이니셜 금지 규칙.
        # 2026-09-11 실측: 'Cheon, Gyeongrok'이 'Cheon, G.'로 줄던 문제).
        keep_full = bool(entry.get("is_en_conversion")) or lang == "ko"
        formatted = [_west_author(a, keep_full=keep_full) for a in authors]
        if len(formatted) == 1:
            s = formatted[0]
        elif len(formatted) == 2:
            s = f"{formatted[0]} & {formatted[1]}"
        else:
            s = ", ".join(formatted[:-1]) + ", & " + formatted[-1]
        if note:
            s += f" {note}" if note.endswith(".") else f" {note}."
        return s
    s = ", ".join(a.strip() for a in authors)
    if note:
        s += f" {note}"
    return s


def normalize_en_line(line: str) -> str:
    """AI가 만든 영문 변환 한 줄의 저자 연결·법령 구두점을 공통기준에 맞춘다.

    - 2인: 'Kim, Hye Jeong, & Heo, Moah (2021)' → 'Kim, Hye Jeong & Heo, Moah (2021)'
      (공통기준 Ⅱ-1)(4) 예시 'Hoffer, J. A. & George, J.' — 쉼표는 3인 이상에서만)
    - 법령: 'Reading Culture Promotion Act, Act No. 21447.' → '…Act. Act No. 21447.'
      (국문 '법령명. 법률 제N호.'와 같은 꼴)
    원고 표기를 재활용한 AI 목록이 이 두 가지를 원고 그대로 두던 문제(2026-09-11 실측).
    """
    line = (line or "").strip()
    m = re.match(r"^(.*?)\s\((\d{4}[a-z]?|n\.d\.)\)", line)
    if m:
        seg = m.group(1)
        # 'Last, First, & Last, First' — 쉼표 3개·& 1개면 2인
        if seg.count("&") == 1 and ", & " in seg and seg.count(",") == 3:
            line = seg.replace(", & ", " & ") + line[len(seg):]
    line = re.sub(r",\s*(Act\s+No\.\s*\d+\.?)$", r". \1", line)
    return line


_EN_YEAR_RE = re.compile(r"^(.*?)\s\((\d{4}[a-z]?|n\.d\.)\)")


def en_line_sort_key(line: str):
    """영문 변환 줄의 알파벳순 정렬 키 — 첫 저자 → 나머지 저자 → 연도 → 전체.

    문자열 통째로 비교하면 'Park, Juhyeon & Byun …'(&=0x26)이 'Park, Juhyeon (2016)'((=0x28)보다
    앞에 와서 공저가 단독 저작보다 앞선다. 첫 저자가 같으면 단독 저작(나머지 저자 없음)이
    먼저, 그다음 공저자 이름순·연도순. 연도 없는 줄(법령)은 첫 문장(법령명)을 이름 자리에 쓴다.
    """
    line = (line or "").strip()
    m = _EN_YEAR_RE.match(line)
    if m:
        seg, year = m.group(1), m.group(2)
    else:
        seg, year = line.split(".")[0], ""
    pieces = [p.strip() for p in re.split(r",\s*&\s*|\s&\s", seg) if p.strip()]
    if not pieces:
        return ("", "", year, line.casefold())
    # 첫 조각 'Han, Cheolwoo, Lee, Kyounghwa'는 '성, 이름' 쌍이 이어진 것 — 두 개씩 묶는다
    toks = [t.strip() for t in pieces[0].split(",")]
    first = ", ".join(toks[:2]) if len(toks) >= 2 else toks[0]
    rest = [", ".join(toks[i:i + 2]) for i in range(2, len(toks), 2)] + pieces[1:]
    return (first.casefold(), "; ".join(rest).casefold(), year, line.casefold())


# ---------------------------------------------------------------- 대소문자

def title_case(s: str) -> str:
    """서양 서명·간행물명: 단어 첫 글자 대문자(관사·전치사 제외, 약어 유지)."""
    if not s or not re.search(r"[A-Za-z]", s):
        return s
    words = s.split()
    out = []
    for i, w in enumerate(words):
        # 부제 경계(콜론·물음표·느낌표) 뒤 첫 낱말은 관사라도 대문자
        # (예: 'Equal Futures? An Imbalance of Opportunities')
        after_break = i > 0 and words[i - 1].endswith((":", "：", "?", "!"))
        if w.isupper() and len(w) >= 2:  # 약어(IFLA, DCF 등) 유지
            out.append(w)
        elif i > 0 and not after_break and w.lower() in _SMALL_WORDS:
            out.append(w.lower())
        else:
            out.append(w[0].upper() + w[1:] if w[0].isalpha() else w)
    return " ".join(out)


def sentence_case(s: str, subtitle: str = "lower") -> str:
    """서양 논문명: 첫 글자만 대문자. 약어·고유명사(내부 대문자 연속어)는 유지.
    이미 소문자 위주면 그대로 두고, Title Case 로 판단될 때만 변환.

    subtitle="lower"(기본, 문편협 공통기준)는 콜론 뒤 부제의 첫 낱말을 소문자로,
    subtitle="upper"는 대문자로 맞춘다 — 학회가 따로 정한 경우(ORG_SUBTITLE_CASE) 쓴다.
    """
    if not s or not re.search(r"[A-Za-z]", s):
        return s
    words = s.split()
    # Title Case 판정: 기능어를 뺀 내용어가 거의 다(80% 이상) 대문자로 시작할 때만.
    # 종전 '전체 낱말의 절반 이상 대문자'는 'Reading in Seoul: The case of Korea'처럼 고유명사가
    # 몇 개 든 문장식 제목까지 Title Case로 보고 전부 낮춰 'seoul'·'korea'를 만들었다(2026-09-21)
    content = [w for w in words[1:] if re.match(r"[A-Za-z]", w) and len(w) >= 3
               and w.lower().strip(",.;:'’") not in _TC_FUNCTION and not (w.isupper() and len(w) >= 2)]
    lower_content = [w for w in content if w[:1].islower()]
    if len(words) > 3 and len(content) >= 2 and len(lower_content) <= len(content) // 5:
        out = []
        for i, w in enumerate(words):
            if w.isupper() and len(w) >= 2:
                out.append(w)
            elif i == 0:
                out.append(w[0].upper() + w[1:].lower() if w[0].isalpha() else w)
            else:
                out.append(w.lower())
        s = " ".join(out)
        # 물음표·느낌표 뒤는 새 문장이므로 대문자
        s = re.sub(r"([?!]\s*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), s)
        if subtitle != "upper":
            return s
    else:
        s = s[0].upper() + s[1:] if s[0].isalpha() else s
    if subtitle == "upper":
        # 부제 첫 낱말을 대문자로 — 한국도서관·정보학회지 57권 2호부터의 규정(ORG_SUBTITLE_CASE)
        return re.sub(r"(:\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), s)
    # 콜론 뒤 부제의 첫 낱말은 소문자 — 학회 오류유형 안내 1.2 '콜론(:) 뒤에 이어지는 부제를
    # 포함한 나머지 단어는 소문자로 유지'(예: A study on digital libraries: user behavior analysis),
    # 공통기준 Ⅱ-1)(5)·6) 예시 'Recent trends in user studies: action research and …'도 같다.
    # APA는 콜론 뒤를 대문자로 쓰므로 원고가 'Why school librarians matter: What years …'처럼
    # 와도 낮춘다(이용자 지적 2026-09-18). 고유명사는 기계가 못 가리므로 관사·의문사·전치사 등
    # 부제 첫머리에 흔한 일반어만 낮추고(이용자 확정 2026-09-21), 그 밖의 대문자 낱말(Korea·IRT)은
    # 저자가 쓴 대로 둔다. 한 글자 'A'를 약어로 오인해 'A new tool'이 남던 문제도 이것으로 해소.
    def _lower_sub(m):
        w = m.group(2)
        if w.lower() in _SUBTITLE_COMMON:
            return m.group(1) + w[0].lower() + w[1:]
        return m.group(0)
    s = re.sub(r"(:\s+)([A-Z][A-Za-z'’\-]*)", _lower_sub, s)
    return s


# 학회별 부제 대소문자 관행 — 문편협 공통기준(기본)은 부제 첫 낱말을 소문자로 두지만,
# 한국도서관·정보학회지는 57권 2호(2026년 6월)부터 참고문헌 부제의 첫 낱말도 대문자로 적는다
# (편집 담당 조은글터 안내, 2026-09-23 이용자 전달). 다른 세 학회지는 종전대로 소문자.
ORG_SUBTITLE_CASE = {"한국도서관정보학회": "upper"}


def subtitle_case_for(org: str) -> str:
    return ORG_SUBTITLE_CASE.get((org or "").strip(), "lower")


# Title Case에서도 소문자로 남는 기능어 — Title Case 판정에서 제외
_TC_FUNCTION = {"a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or", "but", "by", "with",
                "from", "as", "is", "are", "vs", "vs.", "via", "its", "into", "over", "per", "nor", "so",
                "yet", "up", "off", "out", "than", "that", "this", "de", "du", "des", "et", "la", "le"}

# 콜론 뒤 부제의 첫 낱말로 흔한 일반어 — 관사·지시사·의문사·대명사·전치사·접속사와 학술 제목의
# 상투어. 여기 없는 대문자 낱말은 고유명사일 수 있어 그대로 둔다.
_SUBTITLE_COMMON = {
    "a", "an", "the", "this", "that", "these", "those", "its", "our", "their", "his", "her", "some", "any",
    "each", "every", "one", "two", "three", "it", "we", "you", "they", "what", "which", "who", "whom",
    "whose", "how", "why", "when", "where", "whether", "is", "are", "was", "were", "do", "does", "can",
    "should", "toward", "towards", "from", "for", "of", "in", "on", "at", "by", "to", "with", "into",
    "between", "beyond", "under", "over", "through", "during", "after", "before", "about", "against",
    "among", "across", "and", "or", "but", "if", "as", "not", "new", "current", "recent", "evidence",
    "implications", "lessons", "perspectives", "insights", "findings", "results", "case", "cases", "study",
    "studies", "analysis", "review", "comparison", "comparing", "focusing", "focus", "role", "roles",
    "challenges", "trends", "using", "based", "exploring", "examining", "developing", "development",
    "measuring", "assessing", "evaluating", "understanding", "rethinking", "revisiting", "building",
    "designing", "testing", "validation", "effects", "effect", "impact", "impacts", "action",
    "theory", "practice", "policy", "issues", "problems", "prospects", "user", "users", "reading",
    "learning", "teaching", "research", "empirical", "an", "conceptual", "critical", "systematic",
    "mediating", "moderating", "structural", "longitudinal", "qualitative", "quantitative",
}


# ---------------------------------------------------------------- 형식 변환

def format_entry(e: dict, org: str = "") -> str:
    """구조화된 문헌 → 문편협 기준 참고문헌 문자열.

    org를 주면 그 학회지의 편집 관행(ORG_SUBTITLE_CASE)을 얹는다 — 기본 형식은 네 학회가 같다.
    """
    sub_case = subtitle_case_for(org)
    # 구조화가 아무것도 못 건진 항목(제목·저자·수록지 전부 없음)은 빈 껍데기
    # '. (발행년불명).'을 만들지 않고 원문을 그대로 돌려준다 — 어떤 문헌인지
    # 알아볼 수 있어야 '확인 필요' 표시도 의미가 있다.
    if (not (e.get("title") or "").strip() and not (e.get("authors") or [])
            and not (e.get("container") or "").strip()):
        raw = re.sub(r"\s+", " ", (e.get("raw") or "")).strip()
        if raw:
            return raw
    lang = e.get("lang", "ko")
    west = lang == "west"
    t = e.get("type", "unknown")
    year = e.get("year", "") or ("n.d." if west else "발행년불명")
    if e.get("orig_year"):
        year = f"{e['orig_year']}/{e['year']}"
    date = e.get("date", "")

    authors = format_authors(e)
    title = (e.get("title") or "").strip().rstrip(".")
    container = (e.get("container") or "").strip().rstrip(".,")
    if west:
        if t == "journal":
            title = sentence_case(title, sub_case)
            container = title_case(container)
        elif t in ("book", "report", "thesis"):
            # 단독으로 간행되는 저작의 서명은 Title Case(공통기준 Ⅱ-1)(5)).
            # 학위논문도 단행본과 같이 다룬다 — 학회 원고형식 예시
            # 'Functional Requirements for Bibliographic Records: Final Report.'도
            # Title Case다. 문장식으로 낮추면 고유명사(Australia 등)까지 뭉개진다.
            title = title_case(title)
            container = title_case(container)
        elif t in ("newspaper", "conference", "web"):
            # 전자자원의 자원명은 문장식, 웹사이트명은 Title Case — 공통기준 6)전자자원 예시
            # 'McCombes, S. (2020, June 25). How to write a literature review. Scribbr.'
            title = sentence_case(title, sub_case)
            container = title_case(container)

    head_date = f"({date})." if date and t in ("newspaper", "web", "conference", "interview", "av") else f"({year})."
    parts: list[str] = []

    def head():
        if authors:
            parts.append(f"{authors} {head_date}")
        else:
            parts.append(f"{title}. {head_date}")

    if t == "journal":
        head()
        if authors:
            parts.append(f"{title}.")
        seg = container
        vol, iss = (e.get("volume") or "").strip(), (e.get("issue") or "").strip()
        if not vol and iss:
            # 권 없이 통권 번호만 매기는 학술지(국어교육 170, 독서연구 59) — 구조화가 그 번호를 호에
            # 넣으면 권 자리가 비어 번호가 통째로 사라졌다('국어교육, 81-122.' 2026-09-21 신고).
            # 공통기준 예시 'Advances in Consumer Research, 13, 208-212.'처럼 권 자리에 적는다
            vol, iss = iss, ""
        if vol:
            seg += f", {vol}"
            if iss:
                seg += f"({iss})"
        if e.get("pages"):
            seg += f", {e['pages']}"
        elif e.get("article_no"):
            seg += f", {e['article_no']}"
        parts.append(seg + ".")
        if e.get("doi"):
            parts.append(f"https://doi.org/{e['doi']}")
        elif e.get("url"):
            parts.append(e["url"])

    elif t == "book":
        head()
        if authors:
            seg = title
            if e.get("edition"):
                seg += f" ({e['edition']})"
            parts.append(seg + ".")
        elif e.get("edition"):
            parts.append(f"({e['edition']}).")
        if e.get("place") and e.get("publisher"):
            parts.append(f"{e['place']}: {e['publisher']}.")
        elif e.get("publisher"):
            parts.append(f"{e['publisher']}.")

    elif t == "thesis":
        head()
        if authors:
            parts.append(f"{title}.")
        deg = e.get("degree", "")
        if west:
            deg = deg or "Thesis"
        else:
            deg = deg or "학위논문"
        seg = f"{deg}, {e.get('institution', '')}".rstrip(", ")
        if west and e.get("country"):
            seg += f", {e['country']}"
        parts.append(seg + ".")

    elif t == "report":
        head()
        if authors:
            seg = title
            if e.get("report_no"):
                seg += f" ({e['report_no']})"
            parts.append(seg + ".")
        pub = e.get("publisher", "")
        if pub and (not authors or pub not in ", ".join(e.get("authors", []))):
            parts.append(pub + ".")

    elif t == "newspaper":
        head()
        if authors:
            parts.append(f"{title}.")
        seg = container
        if e.get("pages"):
            seg += f", {e['pages']}"
        if seg:
            parts.append(seg + ".")
        if e.get("url"):
            parts.append(e["url"])

    elif t == "web":
        head()
        if authors:
            parts.append(f"{title}.")
        if container:
            parts.append(container + ".")
        if e.get("url"):
            # 접두어는 자료 언어를 따른다: 국문 '출처:', 영문 'Available:'(공통기준 Ⅱ-6·학회 안내 5).
            # 동양문헌(일문·중문)은 기준에 없어 영문과 같이 Available:로(이용자 확정 2026-09-21,
            # 비블리아 편집위원회도 저자의 Available:을 그대로 둠) — 기준 개정(안) Ⅱ-8
            parts.append(("출처: " if lang == "ko" else "Available: ") + e["url"])

    elif t == "conference":
        head()
        if authors:
            parts.append(f"{title}.")
        seg = container
        if e.get("pages"):
            seg += f", {e['pages']}"
        if seg:
            parts.append(seg + ".")

    elif t == "law":
        # 번호가 비면 '법령명..'이 된다 — 번호가 있을 때만 이어 붙인다
        num = (e.get("report_no") or "").strip()
        base = title.rstrip(".")
        return f"{base}. {num}." if num else f"{base}."

    elif t == "standard":
        who = authors or title
        seg = f"{who}. ({year}). " if authors else f"{title}. ({year}). "
        body = e.get("title") if authors else (container or "")
        if authors:
            seg += f"{body}"
        if e.get("report_no"):
            seg += f" ({e['report_no']})"
        seg = seg.rstrip(".") + "."
        if e.get("url"):
            seg += f" {e['url']}"
        return re.sub(r"\s{2,}", " ", seg).strip()

    elif t in ("av", "interview"):
        head()
        if authors:
            seg = title
            if e.get("medium"):
                seg += f" {e['medium']}"
            parts.append(seg + ".")
        if e.get("place") and e.get("publisher"):
            parts.append(f"{e['place']}: {e['publisher']}.")
        elif e.get("publisher"):
            parts.append(e["publisher"] + ".")

    else:  # unknown — 최대한 재구성
        head()
        if authors and title:
            parts.append(f"{title}.")
        if container:
            parts.append(container + ".")
        if e.get("publisher"):
            place = f"{e['place']}: " if e.get("place") else ""
            parts.append(f"{place}{e['publisher']}.")
        if e.get("url"):
            parts.append(e["url"])

    # 온라인 자료의 DOI·URL 보전 — 학위논문·보고서·발표집·단행본 등은 유형별 분기에
    # 출력이 없어서, 원고에 적힌 DOI·주소가 변환 과정에서 통째로 사라지고 있었다.
    # (학술지 논문은 공통기준 Ⅱ-1)(6)에 따라 DOI가 있으면 DOI만 쓴다 — 분기에서 처리)
    if e.get("doi") and not any(e["doi"] in p for p in parts):
        parts.append(f"https://doi.org/{e['doi']}")
    elif e.get("url") and not e.get("doi") and not any(e["url"] in p for p in parts):
        # 출처 접두어 — 공통기준 '국문 웹자료 (출처: URL), 영문 웹자료 (Available: URL)'.
        # 국문은 '출처:'를 전자자원(web)에만 붙인다. web 분기가 이미 붙여 여기까지 오지
        # 않으므로 국문은 접두어 없이 주소만 적는다 — 학회 원고형식의 국문 발표집 예시
        # '국가서지 2030 국제회의 발표집. https://www.oak.go.kr/…'가 접두어 없이 쓴다.
        # 영문은 단독 저작에도 붙인다 — 같은 예시의
        # 'Functional Requirements for Bibliographic Records: Final Report. Available: http://…'.
        # 학술지 논문은 'DOI 또는 URL'을 그대로 적으므로 붙이지 않는다.
        prefix = "Available: " if lang != "ko" and t not in ("journal", "newspaper") else ""
        parts.append(prefix + e["url"])

    s = " ".join(p for p in parts if p and p.strip())
    s = re.sub(r"\s{2,}", " ", s)
    # 문장부 정리('. .' → '.')는 주소 밖에서만 — DOI·URL에는 '..'가 합법적으로
    # 들어간다(예: 10.26589/jockle..103.202501.7). 여기서 뭉개면 링크가 깨진다(실측).
    toks = re.split(r"(https?://\S+|\b10\.\d{4,9}/\S+)", s)
    s = "".join(t if i % 2 else re.sub(r"\.\s*\.", ".", t) for i, t in enumerate(toks))
    return s.strip()


# ---------------------------------------------------------------- 형식 검증

REQUIRED_BY_TYPE = {
    "journal": ["authors", "year", "title", "container"],
    "book": ["authors", "year", "title", "publisher"],
    "thesis": ["authors", "year", "title", "degree", "institution"],
    "report": ["year", "title"],
    "newspaper": ["title", "container"],
    "web": ["title", "url"],
    "conference": ["authors", "title", "container"],
    "law": ["title"],
    "standard": ["title"],
}

FIELD_LABELS = {
    "authors": "저자명", "year": "발행연도", "title": "제목", "container": "게재지·매체명",
    "publisher": "출판사", "degree": "학위명", "institution": "수여기관",
    "pages": "면수", "url": "URL", "place": "출판지",
}


def validate_entry(e: dict) -> list[str]:
    issues = list(e.get("notes", []))
    req = REQUIRED_BY_TYPE.get(e.get("type", ""), ["title"])
    for f in req:
        v = e.get(f)
        if not v or (isinstance(v, list) and not any(v)):
            if e.get("type") in ("law", "standard") and f == "authors":
                continue
            label = FIELD_LABELS.get(f, f)
            msg = f"{label} 누락 — 확인 필요"
            if msg not in issues:
                issues.append(msg)
    if e.get("type") == "journal" and not e.get("pages") and not e.get("article_no"):
        if not any("면수" in i for i in issues):
            issues.append("면수 누락 — 확인 필요(온라인 학술지는 아티클 넘버)")
    elif e.get("type") == "journal" and not e.get("pages") and e.get("article_no"):
        # 사용자 확정(2026-09-07): 아티클 번호는 면수 자리에 유지하되 성격을 설명한다
        issues.append(f"면수 자리의 {e['article_no']}는 쪽수가 아니라 아티클 번호입니다"
                      "(면수 없는 온라인 학술지의 논문 번호 — APA 7판 준용 표기, 수정 불필요)")
    if e.get("type") == "book" and not e.get("place"):
        issues.append("출판지 누락 — 확인 필요")
    return issues


def lost_elements(raw: str, formatted: str) -> list[str]:
    """변환이 원문의 핵심 서지 요소를 잃었는지 검사 — 잃은 요소마다 '확인 필요' 메모.

    표기 변경(재배열·구두점·대소문자)은 정상이므로, 문자 그대로 보존돼야 할 고정
    식별자만 본다: DOI·URL·ISBN·학위 종류·대학교명. 접속일자·(DOI가 있을 때의)
    중복 URL처럼 기준이 의도적으로 없애는 요소는 검사하지 않는다.
    실사용에서 AI가 배치 구조화 중 요소를 산발적으로 빠뜨린 사례의 안전망이다.
    """
    issues: list[str] = []
    if not raw or not formatted:
        return issues
    f_low = formatted.casefold()
    m = re.search(r"\b10\.\d{4,9}/[^\s\"<>]+", raw)
    if m:
        doi = m.group(0).rstrip(".,;)")
        if doi.casefold() not in f_low:
            issues.append(f"원문의 DOI({doi})가 결과에 빠짐 — 확인 필요")
    m = re.search(r"(석사|박사)(?=\s*학위\s*논문)", raw)
    if m and m.group(1) not in formatted:
        issues.append(f"원문의 '{m.group(1)}학위논문' 구분이 결과에 빠짐 — 확인 필요")
    for univ in set(re.findall(r"[가-힣]{2,20}대학교", raw)):
        if univ not in formatted:
            issues.append(f"원문의 기관명({univ})이 결과에 빠짐 — 확인 필요")
    # 영문 학위논문의 수여기관 — 학위 문구 뒤의 기관명이 통째로 사라진 사례 방어(2026-09 실측)
    m = re.search(r"(?i:dissertation|thesis)[\)\.,:]*\s+(?P<u>[A-Z][A-Za-z&.\-' ]{1,60}?)(?=\s*[,\.;]|\s*$)", raw)
    if m and m.group("u").strip().casefold() not in f_low:
        issues.append(f"원문의 기관명({m.group('u').strip()})이 결과에 빠짐 — 확인 필요")
    # 법령 번호('법률 제18547호'·'Act No. 18547') — 구조화가 놓치면 조용히 사라진다
    m = re.search(r"법률\s*제?\s*\d+호|Act\s+No\.?\s*\d+", raw, re.I)
    if m:
        law_no = re.sub(r"\s", "", m.group(0)).casefold()
        if law_no not in re.sub(r"\s", "", formatted).casefold():
            issues.append(f"원문의 법령 번호({m.group(0)})가 결과에 빠짐 — 확인 필요")
    m = re.search(r"ISBN[\s:]*([0-9Xx][0-9Xx\- ]{8,16}[0-9Xx])", raw, re.I)
    if m:
        digits = re.sub(r"[^0-9Xx]", "", m.group(1))
        if digits and digits not in re.sub(r"[^0-9Xx]", "", formatted):
            issues.append("원문의 ISBN이 결과에 빠짐 — 확인 필요")
    m = re.search(r"https?://[^\s\"<>]+", raw)
    if m and "doi.org" not in m.group(0):
        url = m.group(0).rstrip(".,;)")
        # 학술지 논문은 DOI가 있으면 URL을 쓰지 않는 것이 기준(Ⅱ-1(6)) — DOI가 있으면 생략 정상
        if url.casefold() not in f_low and not re.search(r"\b10\.\d{4,9}/", formatted):
            issues.append("원문의 URL이 결과에 빠짐 — 확인 필요")
    return issues


# ---------------------------------------------------------------- 정렬

_LANG_ORDER = {"ko": 0, "west": 1, "east": 2}


def _sort_key(e: dict):
    lang = e.get("lang", "ko")
    authors = e.get("authors") or []
    if authors:
        name = authors[0]
        # 로마자 저자는 lang 표시와 무관하게 '성, 이름' 정규형을 대소문자 구분 없이 비교한다.
        # 변환 항목에 AI가 lang=ko를 달면 'Yang, …'(대문자 그대로)와 'american …'(소문자화)이
        # 섞여 대문자로 시작하는 이름이 전부 앞에 오고, 소문자화된 항목(AASL·법령 2건)이
        # Yang 뒤로 밀리던 문제(2026-09-11 실측). 같은 성은 이름(두문자)순 → 연도순.
        if lang == "west" or not _CJK_RE.search(name):
            name = _west_author(name)
        name = re.sub(r"\s+", " ", name).casefold()
    else:
        name = (e.get("title") or "").casefold()
    year = e.get("year", "")
    ym = re.match(r"(\d{4})([a-z]?)", year or "")
    ynum = int(ym.group(1)) if ym else 9999
    suffix = ym.group(2) if ym else ""   # 같은 저자·연도의 a, b, c는 부기 순서대로
    # 소절 표제로 명시된 '국문 문헌의 영문 변환 표기'는 서양문헌이 아니다 —
    # 국내→서양→동양 원문 뒤에 별도 그룹으로 모아 알파벳순으로 배열한다
    order = 3 if e.get("is_en_conversion") else _LANG_ORDER.get(lang, 0)
    return (order, name, ynum, suffix, (e.get("title") or "").lower())


def _author_year_key(e: dict):
    authors = tuple(a.strip() for a in (e.get("authors") or []))
    year = re.sub(r"[a-z]$", "", e.get("year", "") or "")
    # 원문 그룹과 영문 변환 그룹은 따로 센다 — 서양문헌에 있는 단체 저자가 변환 목록에도
    # 실려 있으면(원고 오류) 원문 쪽이 2012a, 변환 쪽이 2012b가 되던 문제(2026-09-11 실측)
    return (authors, year, bool(e.get("is_en_conversion")))


def sort_and_disambiguate(entries: list[dict]) -> list[dict]:
    """국내→서양→동양, 저자 가나다/알파벳, 연도 오름차순 정렬 후
    동일 저자·동일 연도 문헌에 a, b, c 부기."""
    ordered = sorted(entries, key=_sort_key)
    groups: dict[tuple, list[dict]] = {}
    for e in ordered:
        if not e.get("authors") or not re.match(r"\d{4}$", (e.get("year") or "")):
            continue
        groups.setdefault(_author_year_key(e), []).append(e)
    for key, group in groups.items():
        if len(group) > 1:
            group.sort(key=lambda x: (x.get("title") or "").lower())
            for i, e in enumerate(group):
                e["year"] = f"{key[1]}{chr(ord('a') + i)}"
    return sorted(entries, key=_sort_key)
