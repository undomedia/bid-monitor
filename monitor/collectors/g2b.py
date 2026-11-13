"""나라장터(조달청) 수집기 — 공공데이터포털 Open API.

세 단계를 모두 수집합니다 (기본은 '용역'만):
  발주계획  조달청_나라장터 발주계획현황서비스     https://www.data.go.kr/data/15129462/openapi.do
  사전규격  조달청_나라장터 사전규격정보서비스     https://www.data.go.kr/data/15129437/openapi.do
  입찰공고  조달청_나라장터 입찰공고정보서비스     https://www.data.go.kr/data/15129394/openapi.do

공공데이터포털에서 세 API를 각각 '활용신청' 하면 같은 인증키 하나로 모두 사용할 수 있습니다.
API 주소·오퍼레이션명이 바뀌면 config/sources.yaml 의 base_url / operation 만 고치면 됩니다.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from urllib.parse import unquote

from ..models import Notice
from .base import clean, session

# 단계별 기본값 (sources.yaml 에서 덮어쓸 수 있음)
STAGES = {
    "bid": {
        "category": "입찰공고",
        # 2026년 개편된 '02' 버전 (공공데이터포털 활용신청 화면의 End Point 기준)
        "base_url": "https://apis.data.go.kr/1230000/ad/BidPublicInfoService02",
        "operation": "getBidPblancListInfoServc02",
    },
    "prespec": {
        "category": "사전규격",
        "base_url": "https://apis.data.go.kr/1230000/ao/HrcspSsstndrdInfoService",
        "operation": "getPublicPrcureThngInfoServc",
    },
    "plan": {
        "category": "발주계획",
        "base_url": "https://apis.data.go.kr/1230000/ao/OrderPlanSttusService",
        "operation": "getOrderPlanSttusListServc",
    },
}


def _get(item: dict, *keys: str) -> str:
    """API 버전에 따라 필드명이 조금씩 달라서, 후보 중 처음 값이 있는 것을 사용."""
    for k in keys:
        v = item.get(k)
        if v not in (None, "", " "):
            return str(v).strip()
    return ""


def _to_int(v) -> int | None:
    try:
        n = int(float(str(v).replace(",", "")))
        return n or None
    except (TypeError, ValueError):
        return None


def _fmt_dt(v: str) -> str:
    """'2026-11-11 10:00:00' / '202611111000' / '20261111' → '2026-11-11 10:00' 또는 '2026-11-11'"""
    v = (v or "").strip()
    if not v:
        return ""
    if v.isdigit():                       # 숫자만 붙은 날짜는 자릿수로 구분 (20261112 를 1월 1일로 읽는 실수 방지)
        fmt, out = {8: ("%Y%m%d", "%Y-%m-%d"), 12: ("%Y%m%d%H%M", "%Y-%m-%d %H:%M"),
                    14: ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M")}.get(len(v), (None, None))
        if fmt:
            try:
                return datetime.strptime(v, fmt).strftime(out)
            except ValueError:
                return v
        return v
    for fmt, out in (("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"), ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M"),
                     ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"), ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M"),
                     ("%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M"), ("%Y-%m-%d", "%Y-%m-%d"), ("%Y.%m.%d", "%Y-%m-%d"),
                     ("%Y/%m/%d", "%Y-%m-%d")):
        try:
            return datetime.strptime(v, fmt).strftime(out)
        except ValueError:
            pass
    return v[:16]


def shape(obj, depth: int = 0) -> str:
    """응답의 구조만(값 제외) 짧게 보여준다. 0건일 때 원인 확인용."""
    if depth > 3:
        return "…"
    if isinstance(obj, dict):
        parts = []
        for k, v in list(obj.items())[:12]:
            if k in ("resultMsg", "resultCode", "totalCount", "numOfRows", "pageNo"):
                parts.append(f"{k}={str(v)[:40]}")
            else:
                parts.append(f"{k}:{shape(v, depth + 1)}")
        return "{" + ", ".join(parts) + "}"
    if isinstance(obj, list):
        return f"[{len(obj)}개 " + (shape(obj[0], depth + 1) if obj else "") + "]"
    return type(obj).__name__


def _items(payload: dict) -> tuple[list[dict], int]:
    # 인증키·주소 오류는 다른 형식으로 옴: {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {...}}}
    err = (payload.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader")
    if err:
        hint = {"30": " — 공공데이터포털에서 이 API 활용신청을 했는지, 키를 정확히 넣었는지 확인 (신청 직후엔 1~2시간 걸릴 수 있음)",
                "22": " — 하루 호출 한도 초과",
                "12": " — API 주소가 바뀌었을 수 있음 (sources.yaml 의 base_url/operation 확인)"}
        code = str(err.get("returnReasonCode", ""))
        raise RuntimeError(f"API 오류 {code}: {err.get('returnAuthMsg') or err.get('errMsg')}{hint.get(code, '')}")
    for k, v in payload.items():                 # 예: {"nkoneps.com.response.ResponseError": {"header": {...}}}
        if "ResponseError" in k and isinstance(v, dict):
            h = v.get("header", {}) or {}
            hint = {"07": " — 한 번에 요청한 범위(건수·기간)가 너무 큼", "08": " — 꼭 넣어야 하는 요청 값이 빠짐"}
            code = str(h.get("resultCode", ""))
            raise RuntimeError(f"API 오류 {code}: {h.get('resultMsg')}{hint.get(code, '')}")
    resp = payload.get("response", payload)
    header = resp.get("header", {}) or {}
    code = str(header.get("resultCode", "00"))
    if code not in ("00", "0"):
        raise RuntimeError(f"API 오류 {code}: {header.get('resultMsg')}")
    body = resp.get("body", {}) or {}
    items = body.get("items") or []
    if isinstance(items, dict):           # {"item": [...]} 또는 {"item": {...}}
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    return items, int(body.get("totalCount") or 0)


def _org(item: dict) -> str:
    org = clean(_get(item, "ntceInsttNm", "orderInsttNm", "orderInsttNm1"))
    dmd = clean(_get(item, "dminsttNm", "rlDminsttNm", "dmndInsttNm"))
    if dmd and dmd != org:
        return f"{org} (수요: {dmd})" if org else dmd
    return org


def _files(item: dict, url_keys: list[str], name_keys: list[str]) -> list[dict]:
    """ntceSpecDocUrl1..10 / ntceSpecFileNm1..10 같은 번호 붙은 첨부파일 필드를 목록으로."""
    out, seen = [], set()
    for uk, nk in zip(url_keys, name_keys):
        for i in range(1, 11):
            url = _get(item, f"{uk}{i}")
            if url and url not in seen:
                seen.add(url)
                out.append({"name": _get(item, f"{nk}{i}") or f"첨부{i}", "url": url})
    return out


def _won_text(v) -> str:
    n = _to_int(v)
    return f"{n:,}원" if n else ""


def _info(item: dict, fields: list[tuple[str, tuple[str, ...], str]]) -> dict:
    """(표시이름, 후보 필드들, 형식) → 값이 있는 것만 dict 로."""
    out = {}
    for label, keys, fmt in fields:
        v = _get(item, *keys)
        if fmt == "won":
            v = _won_text(v)
        elif fmt == "dt":
            v = _fmt_dt(v)
        elif fmt == "yn":
            v = {"Y": "예", "N": ""}.get(v, v)
        if v:
            out[label] = clean(v)
    return out


BID_INFO = [
    ("추정가격", ("presmptPrce",), "won"),
    ("배정예산", ("asignBdgtAmt",), "won"),
    ("부가세", ("VAT",), "won"),
    ("계약방법", ("cntrctCnclsMthdNm",), ""),
    ("낙찰방법", ("sucsfbidMthdNm",), ""),
    ("입찰방식", ("bidMethdNm",), ""),
    ("용역구분", ("srvceDivNm",), ""),
    ("입찰개시", ("bidBeginDt",), "dt"),
    ("입찰마감", ("bidClseDt",), "dt"),
    ("개찰일시", ("opengDt",), "dt"),
    ("제안서 제출", ("prpslSbmsnDt", "prpslSubmtDt"), "dt"),
    ("참가자격 등록마감", ("bidQlfctRgstDt",), "dt"),
    ("지역제한", ("prtcptPsblRgnNm", "rgnLmtBidLocplcJdgmBssNm"), ""),
    ("공동수급", ("cmmnSpldmdMethdNm",), ""),
    ("담당자", ("ntceInsttOfclNm",), ""),
    ("연락처", ("ntceInsttOfclTelNo",), ""),
]
PRESPEC_INFO = [
    ("배정예산", ("asignBdgtAmt",), "won"),
    ("의견등록마감", ("opninRgstClseDt",), "dt"),
    ("납품기한", ("dlvrTmlmtDt", "dlvrDaynum"), ""),
    ("담당자", ("ofclNm",), ""),
    ("연락처", ("ofclTelNo",), ""),
]
PLAN_INFO = [
    ("발주금액", ("sumOrderAmt", "orderAmt"), "won"),
    ("발주예정", ("orderYm", "orderMnth"), ""),
    ("계약방법", ("cntrctMthdNm", "cntrctCnclsMthdNm"), ""),
    ("담당부서", ("deptNm",), ""),
    ("담당자", ("ofclNm",), ""),
    ("연락처", ("telNo", "ofclTelNo"), ""),
]


# ---------- 단계별 파서 ----------

def parse_bid(item: dict, src: dict) -> Notice:
    no, ord_ = _get(item, "bidNtceNo"), _get(item, "bidNtceOrd")
    url = _get(item, "bidNtceDtlUrl", "bidNtceUrl") or \
        f"https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo={no}&bidPbancOrd={ord_}"
    kind = clean(_get(item, "ntceKindNm"))          # 등록공고 / 변경공고 / 취소공고 ...
    return Notice(
        source_id=src["id"], source_name=src["name"], category=src["category"],
        title=clean(_get(item, "bidNtceNm")), url=url, uid=f"{no}-{ord_}",
        org=_org(item),
        posted=_fmt_dt(_get(item, "bidNtceDt", "rgstDt"))[:10],
        deadline=_fmt_dt(_get(item, "bidClseDt")),
        amount=_to_int(_get(item, "presmptPrce")) or _to_int(_get(item, "asignBdgtAmt")),
        kind=kind if kind and kind != "등록공고" else "",
        files=([{"name": "입찰공고서(표준)", "url": _get(item, "stdNtceDocUrl")}] if _get(item, "stdNtceDocUrl") else [])
              + _files(item, ["ntceSpecDocUrl"], ["ntceSpecFileNm"]),
        info={**_info(item, BID_INFO),
              **({"사전규격번호": _get(item, "bfSpecRgstNo")} if _get(item, "bfSpecRgstNo") else {})},
    )


def parse_prespec(item: dict, src: dict) -> Notice:
    no = _get(item, "bfSpecRgstNo")
    url = _get(item, "bfSpecDtlUrl", "specDtlUrl") or \
        f"https://www.g2b.go.kr/link/PRVA004_02/single/?bfSpecRegNo={no}"
    return Notice(
        source_id=src["id"], source_name=src["name"], category=src["category"],
        title=clean(_get(item, "prdctClsfcNoNm", "bfSpecNm", "bsnsNm", "prdctDtlList")),
        url=url, uid=no or "",
        org=_org(item),
        posted=_fmt_dt(_get(item, "rcptDt", "rgstDt"))[:10],
        deadline=_fmt_dt(_get(item, "opninRgstClseDt")),     # 의견등록 마감
        amount=_to_int(_get(item, "asignBdgtAmt")),
        kind="",
        files=_files(item, ["specDocFileUrl"], ["specDocFileNm"]),
        info=_info(item, PRESPEC_INFO),
    )


def parse_plan(item: dict, src: dict) -> Notice:
    no = _get(item, "orderPlanUntyNo", "orderPlanNo")
    ym = _get(item, "orderYm", "orderMnth", "orderPlanYm")
    if len(ym) == 6 and ym.isdigit():
        ym = f"{ym[:4]}-{ym[4:]}"
    return Notice(
        source_id=src["id"], source_name=src["name"], category=src["category"],
        title=clean(_get(item, "bizNm", "prdctClsfcNoNm", "cntrctNm")),
        url=_get(item, "orderPlanDtlUrl") or
            f"https://www.g2b.go.kr/link/PNPA001_01/single/?orderPlanUntyNo={no}",
        uid=no or "",
        org=_org(item),
        posted=_fmt_dt(_get(item, "nticeDt", "rgstDt", "rgstDate"))[:10],
        deadline="",
        amount=_to_int(_get(item, "sumOrderAmt", "orderAmt", "asignBdgtAmt")),
        kind=f"발주예정 {ym}" if ym else "",
        info=_info(item, PLAN_INFO),
    )


PARSERS = {"bid": parse_bid, "prespec": parse_prespec, "plan": parse_plan}


def collect(source: dict, lookback_days: float | None = None) -> list[Notice]:
    stage = source.get("stage", "bid")
    defaults = STAGES[stage]
    src = {"name": f"나라장터 {defaults['category']}", "category": defaults["category"], **source}

    key_env = src.get("service_key_env", "G2B_SERVICE_KEY")
    key = os.environ.get(key_env, "").strip()
    if not key:
        raise RuntimeError(f"환경변수 {key_env} 에 공공데이터포털 인증키가 없습니다")
    key = unquote(key)  # Encoding 키를 넣어도 동작하도록

    base = src.get("base_url", defaults["base_url"]).rstrip("/")
    op = src.get("operation", defaults["operation"])
    days = lookback_days if lookback_days is not None else float(src.get("lookback_days", 3))
    end = datetime.now()
    start = end - timedelta(days=days)
    rows, max_pages = int(src.get("rows", 500)), int(src.get("max_pages", 40))
    parse = PARSERS[stage]

    divs = src.get("inqry_divs") or [src.get("inqry_div", 1)]
    notices: list[Notice] = []
    for div in divs:
        notices = _collect_div(src, defaults, key, base, op, start, end, rows, max_pages, parse, div)
        if notices:
            break
    return notices


def _collect_div(src, defaults, key, base, op, start, end, rows, max_pages, parse, div) -> list[Notice]:
    notices: list[Notice] = []
    # API 는 한 번에 조회 가능한 기간이 제한될 수 있어 일정 기간 단위로 나눠 조회
    chunk = timedelta(days=float(src.get("chunk_days", 7)))
    t = start
    while t < end:
        t2 = min(t + chunk, end)
        page = 1
        while page <= max_pages:
            params = {
                "serviceKey": key, "pageNo": page, "numOfRows": rows, "type": "json",
                "inqryDiv": div,                       # 1: 등록(게시)일시 기준
                "inqryBgnDt": t.strftime("%Y%m%d%H%M"),
                "inqryEndDt": t2.strftime("%Y%m%d%H%M"),
            }
            if src.get("stage") == "plan":
                # 발주계획: 조회구분·발주년월범위·게시일자범위로 검색 → 발주년월(이번 달~N개월 뒤)도 함께 보냄
                params["orderBgnYm"] = end.strftime("%Y%m")
                ahead = end + timedelta(days=31 * int(src.get("months_ahead", 6)))
                params["orderEndYm"] = ahead.strftime("%Y%m")
            r = session().get(f"{base}/{op}", params=params, timeout=40)
            if r.status_code >= 400:
                hint = {403: " — 이 API의 활용신청이 안 되었거나 아직 승인 전입니다 (공공데이터포털 마이페이지 확인)",
                        401: " — 인증키가 올바르지 않습니다",
                        429: " — 하루 호출 한도 초과"}.get(r.status_code, "")
                raise RuntimeError(f"{defaults['category']} API 응답 오류 {r.status_code}{hint}")
            try:
                payload = r.json()
            except ValueError:
                # 오류는 XML 로 올 때도 있음
                m = re.search(r"<(?:returnAuthMsg|resultMsg|errMsg)>([^<]+)<", r.text)
                raise RuntimeError(f"API 오류: {m.group(1) if m else r.text[:200]}")
            items, total = _items(payload)
            if page == 1 and t == start:
                # 실행 기록용: 원본 건수와 항목 이름(값은 기록하지 않음)
                print(f"    [{defaults['category']}] 조회기준 {div}: 원본 {total}건, 항목: {sorted(items[0].keys())[:40] if items else '-'}")
                if not items:
                    print(f"      응답 구조: {shape(payload)[:600]}")
            notices.extend(parse(it, src) for it in items)
            if page * rows >= total or not items:
                break
            page += 1
        t = t2
    return notices
