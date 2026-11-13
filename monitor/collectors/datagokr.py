"""공공데이터포털(data.go.kr) 기관 입찰공고 API 범용 수집기.

마사회, 수자원공사처럼 기관마다 따로 제공하는 입찰공고 API를 설정만으로 추가한다.
나라장터와 같은 인증키(G2B_SERVICE_KEY)를 쓴다. 공공데이터포털은 계정당 키가 하나다.

sources.yaml 예:
  - id: kwater_bid
    type: datagokr
    name: 수자원공사
    category: 입찰공고
    url: https://apis.data.go.kr/B500001/.../getBidList   # 활용신청 화면의 End Point + 기능 이름
    params: {pageNo: 1, numOfRows: 100}                    # 기관마다 필요한 추가 값
    date_params: {start: bgnDt, end: endDt, format: "%Y%m%d"}   # 조회 기간 값 이름 (있을 때만)
    fields: {title: bidNm, url: dtlUrl}                    # 항목 이름이 특이할 때만 지정

항목 이름을 모를 때는 흔히 쓰는 이름들을 차례로 시도하고, 실행 기록에 원본 항목 이름을 남긴다.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from urllib.parse import unquote

from ..models import Notice
from .base import clean, session
from .g2b import _fmt_dt, _get, _items, _to_int

# 기관 API에서 흔히 쓰는 항목 이름 후보 (앞에서부터 시도)
CANDIDATES = {
    "title": ["bidNtceNm", "bidNm", "ntceNm", "pblancNm", "bidPblancNm", "cntrctNm", "title", "subject", "nm", "bidName"],
    "no": ["bidNtceNo", "bidNo", "ntceNo", "pblancNo", "bidPblancNo", "seq", "no", "id"],
    "url": ["bidNtceDtlUrl", "dtlUrl", "detailUrl", "url", "link", "linkUrl", "bidNtceUrl"],
    "org": ["ntceInsttNm", "insttNm", "orgNm", "dminsttNm", "deptNm", "orgName"],
    "posted": ["bidNtceDt", "ntceDt", "pblancDt", "regDt", "rgstDt", "registDt", "bidBeginDt", "noticeDate", "pubDate"],
    "deadline": ["bidClseDt", "clseDt", "bidEndDt", "endDt", "rceptEndDt", "submEndDt", "closeDate"],
    "amount": ["presmptPrce", "asignBdgtAmt", "bdgtAmt", "estmtPrce", "prearngPrce", "amt", "budget"],
}


def _pick(item: dict, src: dict, key: str) -> str:
    custom = (src.get("fields") or {}).get(key)
    return _get(item, custom) if custom else _get(item, *CANDIDATES[key])


def _xml_payload(text: str) -> dict:
    """XML 응답을 JSON 과 같은 모양({"response": {"header":…, "body": {"items": [...], "totalCount": n}}})으로 바꾼다."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    if root.find(".//cmmMsgHeader") is not None:          # 인증키·주소 오류
        h = root.find(".//cmmMsgHeader")
        return {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {c.tag: (c.text or "") for c in h}}}
    h = root.find(".//header")
    header = {c.tag: (c.text or "") for c in h} if h is not None else {}
    items = [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]
    total = root.findtext(".//totalCount") or len(items)
    return {"response": {"header": header or {"resultCode": "00"},
                         "body": {"items": items, "totalCount": int(total)}}}


def _payload(r) -> dict:
    text = r.text.lstrip()
    if text.startswith("<"):
        return _xml_payload(text)
    try:
        return r.json()
    except ValueError:
        m = re.search(r"<(?:returnAuthMsg|resultMsg|errMsg)>([^<]+)<", r.text)
        raise RuntimeError(f"API 오류: {m.group(1) if m else r.text[:200]}")


def collect(source: dict, lookback_days: float | None = None) -> list[Notice]:
    key = os.environ.get(source.get("service_key_env", "G2B_SERVICE_KEY"), "").strip()
    if not key:
        raise RuntimeError("환경변수 G2B_SERVICE_KEY 에 공공데이터포털 인증키가 없습니다")
    key = unquote(key)

    days = lookback_days if lookback_days is not None else float(source.get("lookback_days", 7))
    end = datetime.now()
    start = end - timedelta(days=days)
    params = {"serviceKey": key, "type": "json", "_type": "json", "pageNo": 1, "numOfRows": 100}
    params.update(source.get("params") or {})
    dp = source.get("date_params")
    if dp:
        fmt = dp.get("format", "%Y%m%d")
        params[dp["start"]] = start.strftime(fmt)
        params[dp["end"]] = end.strftime(fmt)

    out: list[Notice] = []
    for page in range(1, int(source.get("max_pages", 10)) + 1):
        params["pageNo"] = page
        r = session().get(source["url"], params=params, timeout=40)
        if r.status_code >= 400 and not r.text.lstrip().startswith("<"):
            hint = {403: " — 이 API의 활용신청이 안 되었거나 아직 승인 전입니다"}.get(r.status_code, "")
            raise RuntimeError(f"{source.get('name', '')} API 응답 오류 {r.status_code}{hint}")
        items, total = _items(_payload(r))      # 키 오류 등은 여기서 안내 문구와 함께 알려줌
        if page == 1:
            print(f"    [{source.get('name')}] 원본 {total}건, 항목: {sorted(items[0].keys())[:40] if items else '-'}")
        for it in items:
            title = clean(_pick(it, source, "title"))
            if not title:
                continue
            no = _pick(it, source, "no")
            url = _pick(it, source, "url") or source.get("home", "")
            posted = _fmt_dt(_pick(it, source, "posted"))[:10]
            # 조회 기간 값을 지원하지 않는 API도 있어서, 게시일로 한 번 더 거른다
            if posted and posted < start.strftime("%Y-%m-%d"):
                continue
            out.append(Notice(
                source_id=source["id"], source_name=source.get("name", source["id"]),
                category=source.get("category", "입찰공고"),
                title=title, url=url, uid=no or f"{title}|{posted}",
                org=clean(_pick(it, source, "org")) or source.get("org", source.get("name", "")),
                posted=posted, deadline=_fmt_dt(_pick(it, source, "deadline")),
                amount=_to_int(_pick(it, source, "amount")),
            ))
        if not items or page * int(params.get("numOfRows", 100)) >= total:
            break
    return out
