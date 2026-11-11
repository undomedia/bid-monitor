"""학교장터(S2B) 견적요청/소액수의공고 수집기.

학교·교육청의 소액 용역(예: 학교 홍보영상, 행사 촬영)이 주로 올라오는 곳.
로그인 없이 공개된 '계약현황 > 견적요청/소액수의공고 현황' 화면을 검색어별로 조회한다.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..models import Notice
from .base import clean, session

URL = "https://www.s2b.kr/S2BNCustomer/tcmo001.do"
DETAIL = URL + "?forwardName={view}&estimateCode={code}&tender_step_code=A&page_flag=2"
ITEM = {"물품": "1", "공사": "2", "용역": "3"}


def _search(query: str, start: str, end: str, item: str, page: int) -> str:
    data = {
        "forwardName": "list01", "pageNo": str(page), "search_yn": "Y", "process_yn": "N",
        "estimateCode": "", "tender_step_code": "", "page_flag": "",
        "tender_sep1": "1", "tender_name": query, "company_name_s": "",
        "tender_sep2": "1",                      # 1: 공고일 기준
        "tender_date_start": start, "tender_date_end": end,
        "tender_item": item, "estimate_kind": "", "areaKind": "전국",
    }
    r = session().post(URL, data=urlencode(data, encoding="cp949"), timeout=40,
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    r.raise_for_status()
    r.encoding = "cp949"
    return r.text


def parse(html: str, source: dict) -> list[Notice]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=re.compile(r"f_detail\(")):
        m = re.search(r"f_detail\('(\d+)'\s*,\s*'(\d)'", a["href"])
        if not m:
            continue
        code, kind = m.groups()
        tr = a.find_parent("tr")
        cells1 = [clean(td.get_text(" ")) for td in tr.find_all("td")]
        nxt = tr.find_next_sibling("tr")
        cells2 = [clean(td.get_text(" ")) for td in nxt.find_all("td")] if nxt else []
        # 두 번째 줄: 거래구분 | 기관명 | 공고일 | 견적서제출마감일
        item, org, posted, deadline = (cells2 + ["", "", "", ""])[:4]
        out.append(Notice(
            source_id=source["id"], source_name=source.get("name", "학교장터"),
            category=source.get("category", "학교장터"),
            title=clean(a.get_text()), uid=code,
            url=DETAIL.format(view="view01_1" if kind == "1" else "view01_2", code=code),
            org=org, posted=posted[:10], deadline=deadline[:16],
            kind=" · ".join(x for x in (cells1[1] if len(cells1) > 1 else "", item) if x),
        ))
    return out


def collect(source: dict, lookback_days: float | None = None) -> list[Notice]:
    days = lookback_days if lookback_days is not None else float(source.get("lookback_days", 7))
    days = min(days, 89)                                  # 학교장터는 3개월까지만 조회 가능
    end = datetime.now()
    start = (end - timedelta(days=days)).strftime("%Y%m%d")
    item = ITEM.get(source.get("item", "용역"), "")
    max_pages = int(source.get("max_pages", 10))

    out, seen = [], set()
    for q in source.get("queries", ["영상"]):
        for page in range(1, max_pages + 1):
            rows = parse(_search(q, start, end.strftime("%Y%m%d"), item, page), source)
            for n in rows:
                if n.uid not in seen:
                    seen.add(n.uid)
                    out.append(n)
            if len(rows) < 10:                            # 한 페이지 10건
                break
            time.sleep(0.5)                               # 사이트에 부담 주지 않도록
    return out
