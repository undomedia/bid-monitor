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


FILE_URL = "https://www.s2b.kr/S2BNCustomer/S2B/FileDownload.jsp?FILENAME={name}&UNIQUEFILENAME={uniq}&gubun=estimation"


def _fmt_ymd(v: str) -> str:
    return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if len(v) >= 8 and v[:8].isdigit() else v


def parse_detail(html: str) -> tuple[list[dict], dict]:
    """상세 화면에서 첨부파일과 핵심 정보를 꺼낸다."""
    from urllib.parse import quote
    soup = BeautifulSoup(html, "html.parser")
    files = []
    for name, uniq in re.findall(r"fileDownload\('([^']+)'\s*,\s*'([^']*)'\)", html):
        files.append({"name": name, "url": FILE_URL.format(
            name=quote(name, encoding="cp949"), uniq=quote(uniq or name, encoding="cp949"))})

    info: dict[str, str] = {}
    # 견적제출 공급업체: 1=전체공개, 2=공급업체 선택(업체 지정), 3=참가가능지역 선택
    mode = re.search(r"var\s+estimateCompany\s*=\s*'(\d)'", html)
    if mode:
        regions = []
        for i in range(1, 6):
            o = soup.select_one(f"select[name='limit_region_l{i}'] option[selected]")
            if o and clean(o.get_text()) not in ("", "선택"):
                regions.append(clean(o.get_text()))
        info["_견적방식"] = mode.group(1)
        info["견적제출"] = {"1": "전체공개", "2": "공급업체 지정",
                          "3": "지역제한" + (f" ({', '.join(regions)})" if regions else "")}.get(mode.group(1), "")
        info["_지역"] = ",".join(regions)
    m = re.search(r"var\s+contractDay\s*=\s*'(\d{8})~(\d{8})'", html)
    if m:
        info["계약기간"] = f"{_fmt_ymd(m.group(1))} ~ {_fmt_ymd(m.group(2))}"
    for td in soup.select("td.td_dark_blue"):
        label = clean(td.get_text(" "))
        val = td.find_next_sibling("td")
        if val is None:
            continue
        if label.startswith("분류"):
            opt = val.select_one("select option[selected]")
            if opt:
                info["분류"] = clean(opt.get_text())
        elif label.startswith("계약상대자결정"):
            info["업체 선정"] = clean(val.get_text(" "))
        elif label.startswith("납품장소"):
            loc = val.select_one("input[name='tcmu100VO.deliveryLocation']")
            if loc and loc.get("value"):
                info["납품장소"] = clean(loc["value"])
        elif label.startswith("주요 요청사항"):
            ta = val.find("textarea")
            text = (ta.get_text() if ta else val.get_text("\n")).strip()
            if text:
                info["요청사항"] = re.sub(r"\n{3,}", "\n\n", text)[:1500]
    return files, info


def _exclude_reason(info: dict, source: dict) -> str:
    """견적제출이 전체공개이거나, 참가가능지역에 허용 지역(서울·인천·경기 등)이 있을 때만 표시."""
    mode = info.get("_견적방식")
    if mode == "2":
        return "공급업체 지정"
    if mode == "3":
        allowed = source.get("allowed_regions", ["서울", "인천", "경기"])
        regions = [r for r in info.get("_지역", "").split(",") if r]
        if regions and not any(a in r for r in regions for a in allowed):
            return "참가가능지역 제한: " + ", ".join(regions)
    return ""


def download(url: str) -> tuple[str, bytes]:
    """첨부파일 받기. 학교장터는 중간 페이지가 POST 로 한 번 더 넘기는 구조라 그 단계를 대신 수행한다."""
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(url).query, encoding="cp949")
    name = (q.get("FILENAME") or ["file"])[0]
    uniq = (q.get("UNIQUEFILENAME") or [name])[0]
    data = {"forwardName": "download", "file_name": name, "path_from": "estimation",
            "manage_file_name": uniq, "sub_dir": ""}
    r = session().post("https://www.s2b.kr/S2BNCustomer/commonFile.do",
                       data=urlencode(data, encoding="cp949"), timeout=60,
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    r.raise_for_status()
    return name, r.content


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
    if source.get("details", True):
        for n in out[: int(source.get("max_details", 300))]:
            try:
                r = session().get(n.url, timeout=30)
                r.encoding = "cp949"
                n.files, n.info = parse_detail(r.text)
                n.exclude_reason = _exclude_reason(n.info, source)
                n.info = {k: v for k, v in n.info.items() if not k.startswith("_")}
            except Exception:  # noqa: BLE001 — 상세를 못 읽어도 목록은 유지
                pass
            time.sleep(0.3)
    return out
