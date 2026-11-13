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

FIELDS = {
    "title": ["name", "bidName", "noticeName", "title"],
    "no": ["no", "noticeNo", "bidNo"],
    "org": ["companyName", "companyNm", "company", "orgName", "deptName"],
    "posted": ["noticeDate", "noticeDatetime", "noticeBeginDate", "regDate", "pubDate"],
    "deadline": ["bidAttendReqCloseDatetime", "bidCloseDatetime", "bidEndDatetime", "closeDatetime", "noticeEndDate"],
    "amount": ["estimatedPrice", "budgetAmount", "presmptPrce", "estimatePrice", "amount"],
    "url": ["url", "detailUrl", "link"],
    "kind": ["contractType", "contractMethod", "bidType", "purchaseType"],
}


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
        out.append(Notice(
            source_id=source["id"], source_name=source.get("name", "한국전력"),
            category=source.get("category", "입찰공고"),
            title=title, url=_get(it, *FIELDS["url"]) or HOME, uid=no or title,
            org=clean(_get(it, *FIELDS["org"])) or "한국전력공사",
            posted=_fmt_dt(_get(it, *FIELDS["posted"]))[:10],
            deadline=_fmt_dt(_get(it, *FIELDS["deadline"])),
            amount=_to_int(_get(it, *FIELDS["amount"])),
            kind=clean(_get(it, *FIELDS["kind"])),
        ))
    return out
