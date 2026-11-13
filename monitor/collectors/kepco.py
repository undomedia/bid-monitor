"""한국전력공사 전자입찰계약정보 (전력데이터 개방포털 OPEN API).

요청 주소: https://bigdata.kepco.co.kr/openapi/v1/electContract.do
필수 값: noticeBeginDate, noticeEndDate (YYYYMMDD, 최대 90일)
companyId 를 비우면 한전과 발전 자회사(서부·남부·중부·남동·동서발전, 한전KPS 등) 공고를 모두 받는다.
인증키는 전력데이터 개방포털에서 따로 발급 → 환경변수 KEPCO_API_KEY.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from ..models import Notice
from .base import clean, session
from .g2b import _fmt_dt, _get, _to_int

URL = "https://bigdata.kepco.co.kr/openapi/v1/electContract.do"
HOME = "https://srm.kepco.net/"

FIELDS = {   # 전력데이터 개방포털 '전자입찰 계약정보 가이드'의 응답 결과 항목
    "title": ["name"],
    "no": ["no"],
    "org": ["placeName", "contractReqDepartmentName"],
    "posted": ["noticeDate", "createDatetime"],
    "deadline": ["bidAttendReqCloseDatetime", "endDatetime"],
    "amount": ["presumedPrice", "presumedAmount", "estimatedPriceBasicAmount", "subcontractBudgetAmount"],
    "url": ["url"],
}

COMPANY = {"COM01": "한국전력공사", "COM02": "한국서부발전", "COM03": "한국전력국제원자력대학원대학교",
           "COM04": "한국남부발전", "COM05": "한국중부발전", "COM06": "한국남동발전", "COM08": "한국동서발전",
           "COM09": "한국전력기술", "COM10": "한전KPS", "COM11": "한국전력거래소", "COM12": "한국원자력연료",
           "COM14": "한국발전교육원", "COM16": "한국해상풍력", "COM19": "KAPES"}
COMPETITION = {"Open": "일반경쟁", "Destination": "지명경쟁", "Limited": "제한경쟁", "Private": "수의계약"}
BID_TYPE = {"LimitedLowestPrice": "제한적최저가", "LowestPrice": "최저가", "QualifiedEval": "적격심사",
            "CollectivelyBid": "일괄입찰", "Nego": "협상에 의한 계약", "TotalEvalSuccess": "종합심사낙찰제"}
ITEM_TYPE = {"Construction": "공사", "Service": "용역"}
PURCHASE = {"Product": "자재구매", "ConstructionService": "공사용역"}


def _info(it: dict) -> dict:
    def g(k):
        return clean(_get(it, k))
    info = {
        "추정가격": f"{_to_int(g('presumedPrice') or g('presumedAmount')):,}원" if _to_int(g("presumedPrice") or g("presumedAmount")) else "",
        "계약방법": COMPETITION.get(g("competitionType"), g("competitionType")),
        "낙찰방법": BID_TYPE.get(g("bidType"), g("bidType")),
        "낙찰자 결정": g("bidTypeDetail"),
        "입찰신청 마감": _fmt_dt(g("bidAttendReqCloseDatetime")),
        "투찰 시작": _fmt_dt(g("beginDatetime")),
        "입찰 종료": _fmt_dt(g("endDatetime")),
        "계약 착수일": g("contractBeginEndDate"),
        "납기일": g("deliveryDueDate"),
        "참가자격": g("bidAttendRestrict") or g("etc"),
        "공동수급": {"1": "가능", "0": "불가"}.get(g("jointSupplyDemandYn"), ""),
        "현장설명회": ({"1": "있음", "0": "없음"}.get(g("fieldIntroYn"), "")
                    + (f" ({_fmt_dt(g('fieldIntroBeginDatetime'))})" if g("fieldIntroBeginDatetime") else "")),
        "계약의뢰부서": g("contractReqDepartmentName"),
        "담당자": g("creatorName"),
    }
    return {k: v for k, v in info.items() if v}


def _files(it: dict) -> list[dict]:
    out = []
    for i in range(1, 11):
        link = _get(it, f"filenlink{i}", f"filelink{i}")
        if link:
            out.append({"name": _get(it, f"filename{i}") or f"첨부{i}", "url": link})
    return out


def _rows(payload) -> list[dict]:
    """응답 모양이 확실하지 않아 흔한 모양을 차례로 확인한다."""
    if isinstance(payload, list):
        return payload
    for k in ("data", "list", "items", "item", "result", "body"):
        v = payload.get(k) if isinstance(payload, dict) else None
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            inner = _rows(v)
            if inner:
                return inner
    return []


def collect(source: dict, lookback_days: float | None = None) -> list[Notice]:
    key = os.environ.get("KEPCO_API_KEY", "").strip()
    if not key:
        raise RuntimeError("환경변수 KEPCO_API_KEY 에 전력데이터 개방포털 인증키가 없습니다")
    days = min(lookback_days if lookback_days is not None else float(source.get("lookback_days", 14)), 89)
    end = datetime.now()
    start = end - timedelta(days=days)
    params = {"apiKey": key, "returnType": "json",
              "noticeBeginDate": start.strftime("%Y%m%d"), "noticeEndDate": end.strftime("%Y%m%d")}
    if source.get("company_id"):
        params["companyId"] = source["company_id"]
    r = session().get(source.get("url", URL), params=params, timeout=40)
    if r.status_code >= 400:
        raise RuntimeError(f"한전 API 응답 오류 {r.status_code} — 인증키 또는 요청 값을 확인하세요")
    try:
        payload = r.json()
    except ValueError:
        raise RuntimeError(f"한전 API 응답을 읽지 못했습니다: {r.text[:150]}")
    rows = _rows(payload)
    print(f"    [한국전력] 원본 {len(rows)}건, 항목: {sorted(rows[0].keys())[:40] if rows else '-'}"
          f"{'' if rows else ', 응답 최상위: ' + str(list(payload)[:10] if isinstance(payload, dict) else type(payload).__name__)}")
    out = []
    for it in rows:
        title = clean(_get(it, *FIELDS["title"]))
        if not title:
            continue
        no = _get(it, *FIELDS["no"])
        company = COMPANY.get(_get(it, "companyId"), "")
        out.append(Notice(
            source_id=source["id"], source_name=company or source.get("name", "한국전력"),
            category=source.get("category", "입찰공고"),
            title=title, url=_get(it, *FIELDS["url"]) or HOME, uid=no or title,
            org=clean(_get(it, *FIELDS["org"])) or company or "한국전력공사",
            posted=_fmt_dt(_get(it, *FIELDS["posted"]))[:10],
            deadline=_fmt_dt(_get(it, *FIELDS["deadline"])),
            amount=_to_int(_get(it, *FIELDS["amount"])),
            kind=" · ".join(x for x in (ITEM_TYPE.get(_get(it, "itemType"), ""),
                                        PURCHASE.get(_get(it, "purchaseType"), "")) if x),
            files=_files(it), info=_info(it),
        ))
    return out
