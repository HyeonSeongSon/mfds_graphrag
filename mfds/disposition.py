"""식품 행정처분(I0470) 행 읽기 · 위반 문구 라벨 정규화 (1주차 D4에 잠금). 적재기와 평가셋 생성기가 같은 함수를 쓴다.

- `read_rows`: 스냅숏의 I0470 행(수동 수집 CSV 또는 API JSON). 기본으로 업소 정보 열을 지운다.
  업소 정보가 필요한 곳은 평가셋 생성기의 같은 업소 이웃 조건뿐이고, 메모리에서만 쓴다(`keep_pii=True`)
- `scrub`: 위반 내용 · 처분 내용 원문 안의 그 업소 업소명 · 인허가번호 등을 `<업소명>` · `<인허가번호>`로 가린다.
  `read_rows`가 `keep_pii`와 상관없이 늘 적용하므로 그래프 속성과 평가 문항 텍스트가 같다
- `violation_text`: `Disposition.violation_text` 값이자 `m2_disposition` 문항 라벨. 원문 앞 `(YYYYMMDD)`만 떼고
  양끝 공백을 정리한다(그 밖의 날짜 · 차수는 남긴다. T1 묶음 키 정규화는 `eval/grouping.normalize_violation`)

수집 코드(`mfds/collect/foodsafety.py`)는 잠그지 않으므로, 잠금 대상인 읽기 · 라벨 함수만 이 모듈에 둔다.

의약품 · 화장품 처분(T7, 공공데이터포털 `MdcinExaathrService04`, 수집은 `mfds/collect/drugdisp.py`):

- `read_drug_rows`: 스냅숏의 drugdisp 행. 업체명 · 주소 · 사업자번호 · 허가번호를 본문에서 가리고(`scrub`,
  법인격 표기를 뗀 핵심 이름 포함), 인명 규칙(`mask_person_names`)으로 인명을 `<인명>`으로 가린 뒤, 기본으로
  업체 정보 열을 지운다. 폴더가 없으면 빈 목록
- `split_basis` · `basis_articles`: 근거법령(`BEF_APPLY_LAW`)을 줄마다 위반 / 처분 / 표지 없음으로 나누고, 위반 부분
  조문만 정답 · `BASED_ON`으로 쓴다(처분 조문 `DISPOSITION_ARTICLES`는 늘 뺀다). 조문 해석 함수는 인자로 받는다
  (이 모듈은 `mfds.refs`를 import하지 않는다)
- 생성기와 적재기가 같은 분할을 쓰므로 분할 오류는 그래프 대조로 잡히지 않는다(정답 독립성 예외, PREREG 4-1).
  보완은 544건 전수 분할 보고와 T7 라벨 검수다
"""

import csv
import gzip
import io
import json
import re
from pathlib import Path

SERVICE = "I0470"
# 업소명 · 주소 · 전화 · 대표자 · 인허가번호
PII = ("PRCSCITYPOINT_BSSHNM", "ADDR", "TELNO", "PRSDNT_NM", "LCNS_NO")
# 자유 텍스트 열(위반 내용 · 처분 내용). 원문에 그 업소의 업소명 · 인허가번호가 들어 있는 처분이 있다
# (스냅숏 20261105 기준 처분 17건: 위반 내용 17(업소명 14 · 인허가번호 4), 그중 1건은 처분 내용에도. 10-29 스냅숏은 14건)
TEXT_FIELDS = ("VILTCN", "DSPSCN")
# (열, 가림 표시, 최소 길이(공백 · 하이픈 뺀 글자 수)). 짧은 값은 흔한 낱말과 겹쳐 가리지 않는다
_MASKS = (("PRCSCITYPOINT_BSSHNM", "<업소명>", 2), ("PRSDNT_NM", "<대표자>", 2), ("ADDR", "<주소>", 10),
          ("LCNS_NO", "<인허가번호>", 6), ("TELNO", "<전화>", 8))
_DATE_PREFIX = re.compile(r"^\s*\(\d{8}\)")

# --- 의약품 · 화장품 처분(drugdisp) ---
DRUG_SOURCE = "drugdisp"
DRUG_PII = ("ENTP_NAME", "ADDR", "BIZRNO", "ENTP_NO")
DRUG_TEXT_FIELDS = ("EXPOSE_CONT", "ADM_DISPS_NAME")
# ADDR은 시 · 군 · 구까지만 있는 짧은 값이 많다(최대 15자). ENTP_NO는 대개 8자리, 짧은 번호(예: 3자리)는 가리지 않는다
_DRUG_MASKS = (("ENTP_NAME", "<업체명>", 2), ("ADDR", "<주소>", 10), ("BIZRNO", "<사업자번호>", 10),
               ("ENTP_NO", "<허가번호>", 6))
PERSON = "<인명>"
_MASK = re.compile("|".join(re.escape(m) for m in
                            dict.fromkeys([m for _, m, _ in _MASKS] + [m for _, m, _ in _DRUG_MASKS] + [PERSON])))


def _value_pattern(value: str, min_len: int) -> re.Pattern | None:
    chars = [c for c in value or "" if not c.isspace() and c != "-"]
    return re.compile(r"[\s\-]*".join(map(re.escape, chars))) if len(chars) >= min_len else None


# 업체명 앞뒤 법인격 표기. 위반 문구에는 "○○제약이 …"처럼 핵심 이름만 나오기 쉽다
_CORP_WORDS = ("(주)", "㈜", "주식회사", "(유)", "유한회사", "유한책임회사", "합자회사", "합명회사", "(재)", "재단법인",
               "사단법인", "(사)", "농업회사법인")
_CORP = "(?:" + "|".join(map(re.escape, _CORP_WORDS)) + ")"
_CORP_EDGE = re.compile(rf"^\s*{_CORP}\s*|\s*{_CORP}\s*$")
CORE_MIN = 3  # 핵심 이름 최소 글자 수. "동아 · 한미" 같은 2글자는 "동아시아" 같은 낱말을 망가뜨려 가리지 않는다(한계)


def core_name(name: str) -> str:
    """법인격 표기를 앞뒤에서 뗀 핵심 이름."""
    prev = None
    while prev != name:
        prev, name = name, _CORP_EDGE.sub("", name)
    return name.strip()


def scrub(row: dict[str, str], masks=_MASKS, fields=TEXT_FIELDS) -> dict[str, str]:
    """자유 텍스트 열에서 그 행 업소의 업소 정보 값을 가림 표시로 바꾼다(글자 사이 공백 · 하이픈 무시).
    다른 업소 이름은 알 수 없어 가리지 못한다(한계). 기본값은 식품 처분(I0470)."""
    pats = []
    for col, mark, min_len in masks:
        p = _value_pattern(row.get(col) or "", min_len)
        if p:
            pats.append((p, mark))
    if masks is _DRUG_MASKS:  # 핵심 이름: 3글자 이상, 바로 앞이 한글이 아닐 때만(뒤의 조사는 허용)
        core = core_name(row.get("ENTP_NAME") or "")
        if core != (row.get("ENTP_NAME") or "").strip():
            p = _value_pattern(core, CORE_MIN)
            if p:  # 앞 경계: 한글이 아니거나 바로 앞이 법인격 표기("주식회사가짜제약")
                left = "|".join(f"(?<={re.escape(c)})" for c in _CORP_WORDS)
                pats.append((re.compile(rf"(?:{left}|(?<![가-힣])){p.pattern}"), "<업체명>"))
    out = dict(row)
    for f in fields:
        text = out.get(f)
        if text:
            for p, mark in pats:
                text = p.sub(mark, text)
            out[f] = text
    return out


def has_mask(text: str | None) -> bool:
    """가림 표시가 든 텍스트인가. 위반 문구를 질문에 넣는 문항(T1 · m2 처분)은 이런 처분을 원천으로 뽑지 않는다(PREREG 4-2)."""
    return bool(_MASK.search(text or ""))


def read_rows(snapshot: Path, keep_pii: bool = False) -> list[dict[str, str]]:
    """스냅숏의 I0470 행(수동 수집 CSV 또는 API JSON). 자유 텍스트 안의 업소 정보는 늘 가리고(`scrub`),
    기본으로 업소 정보 열도 지운다."""
    path = snapshot / "foodsafety" / f"{SERVICE}.csv.gz"
    if path.exists():
        with gzip.open(path, "rb") as f:
            rows = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))
    else:
        pages = sorted((snapshot / "foodsafety" / SERVICE).glob("*.json"))
        if not pages:
            raise FileNotFoundError(f"{snapshot}에 {SERVICE}가 없다")
        rows = [r for p in pages for r in json.loads(p.read_bytes())[SERVICE].get("row", [])]
    rows = [scrub(r) for r in rows]
    if not keep_pii:
        rows = [{k: v for k, v in r.items() if k not in PII} for r in rows]
    return rows


def violation_text(raw: str | None) -> str:
    """위반 내용 원문 → 라벨. 앞 `(YYYYMMDD)` 날짜만 떼고 양끝 공백을 정리한다."""
    return _DATE_PREFIX.sub("", raw or "").strip()


# --- 인명 규칙(의약품 · 화장품 처분). 대표자 열이 없어 같은 행 값으로 가릴 수 없으므로 규칙으로 가린다 ---
# 성씨 조건으로 일반어("관리약사 · 무자격약사 · 대표자")를 거른다. 상위 성씨(한 글자)만 쓴다
SURNAMES = "김이박최정강조윤장임한오서신권황안송류유전홍고문양손배백허남심노하곽성차주우구민진나지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉"
# 이름 자리에 오면 일반 낱말인 앞 두 글자(전수 보고로 보충). "약사 이외의 자"가 인명으로 잡히면 약사법의 흔한 위반
# 유형이 평가에서 체계적으로 빠지므로 막는다
NAME_STOP = frozenset((
    "이외 이사 이하 이상 이후 이전 이용 이력 이송 이행 이첩 조제 조사 조치 조건 조회 조합 정관 정당 정보 정상 정해 정기 정지 "
    "장소 장부 장기 임의 임상 임대 한정 한약 한도 안전 안내 안정 유통 유효 유지 유사 전문 전용 전자 전부 전체 주의 주소 주문 "
    "주성 주요 성분 성명 성능 고객 고지 고시 신고 신청 신규 서류 서면 방문 공급 공정 공고 표시 표기 문서 문구 하여 하지 기간 "
    "기준 기재 기타 도매 소매 소비 변경 편의 제조 제품 제출 진열 진료 경고 경우 명칭 명의 반품 왕래 모든 국내 국외 용기 용량 "
    "예정 경과 봉함 마약 원료 원장 허가 노출 나머 나이 지정 지시 지도 채취 천연 현장 함량 연구 위반 위탁 위해 구매 구입 민원 "
    "인정 인증 인터 인체 금지 옥외 육안 탁송 어린 은닉 손상 배송 배합 백신 남용 심사 송부 차량 오남 오류 우편 권한 황사 "
    "고용 소속 공동 현직 임시 이번 정식 차기 전임 신임 원래 우리 근무 상근 담당 해당 주재 상주 대리 대체 겸직 겸임 "
    "약국 약품 업체 회사 선생 고객 회원 손님 환자").split())
_TITLES = "대표자|개설자|관리약사|약사|원장|대표"
_JOSA_AFTER = r"(?=씨|님|은|는|이|가|을|를|의|에|와|과|로|도|께|\s|[,.)\]」』'\"·:;]|$)"
_SUR = f"[{SURNAMES}]"
PERSON_RULES = (
    ("이름+직함", re.compile(rf"(?<![가-힣])({_SUR}[가-힣]{{1,3}})\s?(?:씨|약사|대표|원장|님){_JOSA_AFTER}")),
    ("직함+이름", re.compile(rf"(?<![가-힣])(?:{_TITLES})\s?({_SUR}[가-힣]{{2}}){_JOSA_AFTER}")),
    ("부분 가림", re.compile(rf"(?<![가-힣])({_SUR}[OＯ○◯●*＊×Xx]{{1,2}}[가-힣]?)(?![A-Za-z])")),
)


def mask_person_names(text: str, stats: dict | None = None) -> str:
    """인명 규칙에 걸린 이름 구간을 `<인명>`으로 바꾼다(직함은 남긴다). `stats`가 있으면 규칙별 건수를 더한다.
    직함 없이 이름만 나오는 경우("홍길동이 …")는 잡지 못한다(한계)."""
    for name, pat in PERSON_RULES:
        def repl(m: re.Match) -> str:
            who = m.group(1)
            if name != "부분 가림" and who[:2] in NAME_STOP:
                return m.group(0)
            if stats is not None:
                stats[name] = stats.get(name, 0) + 1
            return m.group(0).replace(who, PERSON, 1)
        text = pat.sub(repl, text or "")
    return text


def read_drug_rows(snapshot: Path, keep_pii: bool = False, stats: dict | None = None) -> list[dict[str, str]]:
    """스냅숏의 의약품 · 화장품 처분 행(drugdisp JSON). 본문의 업체 정보(`scrub`)와 인명(`mask_person_names`)은
    `keep_pii`와 상관없이 늘 가리고, 기본으로 업체 정보 열도 지운다. 폴더가 없으면 빈 목록."""
    pages = sorted((snapshot / DRUG_SOURCE).glob("p*.json"))
    rows = []
    for p in pages:
        items = json.loads(p.read_bytes())["body"].get("items") or []
        rows += [items] if isinstance(items, dict) else items
    out = []
    for r in rows:
        r = {k: ("" if v is None else str(v)) for k, v in r.items()}
        r = scrub(r, _DRUG_MASKS, DRUG_TEXT_FIELDS)
        for f in DRUG_TEXT_FIELDS:
            r[f] = mask_person_names(r.get(f, ""), stats)
        if not keep_pii:
            r = {k: v for k, v in r.items() if k not in DRUG_PII}
        out.append(r)
    return out


def drug_violation_text(raw: str | None) -> str:
    """의약품 · 화장품 위반내용 원문 → 라벨 · 질문 원천. 줄바꿈 · 연속 공백을 한 칸으로, 앞머리 글머리표를 뗀다."""
    text = re.sub(r"\s+", " ", raw or "").strip()
    return re.sub(r"^[○￮◦ㅇ\-•·\s]+", "", text).strip()


# --- 근거법령 분할(정답 규칙, PREREG 4-2 T7) ---
_LINE_SPLIT = re.compile(r"\n|(?=[○￮◦])")
_VIOL_LABEL = re.compile(r"^\W*위반\s*법령")
_DISP_LABEL = re.compile(r"^\W*(?:처분\s*근거\s*법령|처분\s*법령|행정\s*처분\s*기준|처분\s*기준|처분\s*근거)")
_DISP_TEXT = re.compile(r"행정\s*처분\s*의?\s*기준")
_SUB_BULLET = re.compile(r"^\s*[-·•ㆍ]")
_ARTICLE_MENTION = re.compile(r"제\s*\d+\s*조|「")


_DOUBLE_OPEN = re.compile(r"「(?:\s*「)+")
_DOUBLE_CLOSE = re.compile(r"」(?:\s*」)+")


# 항 · 호 바로 뒤(공백만 사이)에 붙은 "제N조". 원문에서는 오타("제76조제1항제3조" = 제3호)일 수도, 쉼표 누락(판례
# "제37조 제1항제37조 제2항")일 수도 있어 고쳐 읽지 않는다. 위반 쪽에 있으면 그 처분을 T7 원천에서 뺀다.
_GLUED_ARTICLE = re.compile(
    r"제\s*\d+\s*조(?:\s*의\s*\d+)?"
    r"(?:\s*제\s*\d+\s*항(?:\s*제\s*\d+\s*호(?:\s*의\s*\d+)?)?|\s*제\s*\d+\s*호(?:\s*의\s*\d+)?)"
    r"\s*제\s*\d+\s*조")


def glued_article(line: str | None) -> bool:
    """항 · 호 뒤에 구분자 없이 조가 붙은 표기가 있는가(오타 의심, 고쳐 읽지 않음)."""
    return bool(_GLUED_ARTICLE.search(line or ""))


def normalize_brackets(text: str) -> str:
    """｢｣ → 「」, 겹친 낫표(「「약사법」 · 원문 오타 2건)는 하나로. 겹친 채 두면 법령명을 못 읽어 그 조문이 빠진다."""
    t = (text or "").replace("｢", "「").replace("｣", "」")
    return _DOUBLE_CLOSE.sub("」", _DOUBLE_OPEN.sub("「", t))


def split_basis(text: str | None) -> list[tuple[str, str]]:
    """근거법령 → [(라벨, 줄)]. 라벨은 violation · disposition · unlabeled. 표지("위반법령" / "처분근거법령 ·
    처분법령 · 행정처분기준")와 "행정처분의 기준" 문구로만 붙인다. [별표]는 위반 쪽 별표(예: 표시 · 광고 준수사항)도
    있어 라벨 근거로 쓰지 않는다. "근거 법령" 같은 모호한 표지는 unlabeled."""
    out, header = [], None
    for line in _LINE_SPLIT.split(normalize_brackets(text)):
        line = line.strip().strip('"').strip()
        if not line.strip("○￮◦ㅇ-•· "):
            continue
        if _VIOL_LABEL.search(line):
            label = "violation"
        elif _DISP_LABEL.search(line) or _DISP_TEXT.search(line):
            label = "disposition"
        elif header and _SUB_BULLET.match(line):  # 머리 줄("○ 위반법령") 아래 하위 줄("- …")은 머리 줄 라벨을 잇는다
            label = header
        else:
            label = "unlabeled"
        if label != "unlabeled" and not _ARTICLE_MENTION.search(line):
            header = label  # 조문 없는 표지 줄 = 머리 줄
        elif not _SUB_BULLET.match(line):
            header = None
        out.append((label, line))
    return out


# 처분 조문: 위반할 수 있는 조문이 아니므로 정답 · BASED_ON에서 늘 뺀다. 2026-11-20 전수 보고(544건)에서 처분 줄에만
# 나오고 위반 줄에는 없는(약사법 제76조만 1회) 조문과, 같은 성격(과징금 · 지정취소)의 조문이다
DISPOSITION_ARTICLES = frozenset((
    "art:001783:76",    # 약사법 제76조 허가취소와 업무정지 등
    "art:001783:76-2",  # 약사법 제76조의2 임상시험실시기관 지정취소 등
    "art:001783:81",    # 약사법 제81조 업무정지 처분을 갈음하여 부과하는 과징금 처분
    "art:011794:95",    # 의약품 등의 안전에 관한 규칙 제95조 행정처분의 기준
    "art:002015:24",    # 화장품법 제24조 등록의 취소 등
    "art:002015:28",    # 화장품법 제28조 과징금처분
    "art:008741:29",    # 화장품법 시행규칙 제29조 행정처분의 기준
))


def basis_articles(text: str | None, resolve, disposition_articles=DISPOSITION_ARTICLES) -> dict:
    """근거법령 → {"violation": [조문 uid], "violation_refs": [위반 부분 항 · 호 uid], "disposition": [조문 uid],
    "unresolved": 해석 실패 수, "dropped": 위반 줄에서 뺀 처분 조문 수, "glued_violation" · "glued_disposition": 항 · 호 뒤에
    조가 붙은 표기(`glued_article`, 오타 의심)가 든 위반 · 처분 줄 수, "lines": [(라벨, 줄)]}. `resolve(text)`는 `Resolver.resolve`(결과에 `.refs` ·
    `.failures`). 상대 표기("같은 법 시행규칙")의 문맥은 같은 라벨의 가장 가까운 앞 줄로만 한정한다. 표지 없는 줄은
    조문이 모두 처분 조문이면 처분 부분으로 본다. 조문 uid는 항 · 호를 조문으로 올리고 별표는 뺀다."""
    lines = split_basis(text)
    viol, viol_refs, disp, unresolved, dropped, last = [], [], [], 0, 0, {}
    glued = {"violation": 0, "disposition": 0}
    for label, line in lines:
        prev = last.get(label)
        res = resolve(f"{prev} {line}" if prev else line)
        base = [r.uid for r in resolve(prev).refs] if prev else []
        refs = list(res.refs)
        for u in base:  # 앞 줄 몫을 뺀다(같은 uid가 겹치면 한 번씩)
            for k, r in enumerate(refs):
                if r.uid == u:
                    del refs[k]
                    break
        unresolved += max(len(res.failures) - (len(resolve(prev).failures) if prev else 0), 0)
        arts = list(dict.fromkeys(r.article_uid for r in refs if r.article_uid and not r.uid.startswith("anx:")))
        last[label] = line
        if label == "unlabeled":
            label = "disposition" if arts and set(arts) <= disposition_articles else "violation"
        if glued_article(line):
            glued[label] += 1
        if label == "violation":
            keep = [a for a in arts if a not in disposition_articles]
            dropped += len(arts) - len(keep)
            viol += keep
            viol_refs += [r.uid for r in refs if r.article_uid in keep and not r.uid.startswith("anx:")]
        else:
            disp += arts
    return {"violation": list(dict.fromkeys(viol)), "violation_refs": list(dict.fromkeys(viol_refs)),
            "disposition": list(dict.fromkeys(disp)), "unresolved": unresolved, "dropped": dropped,
            "glued_violation": glued["violation"], "glued_disposition": glued["disposition"], "lines": lines}
