"""범용 게시판 수집기.

기관 홈페이지 견적공고, 공모전 사이트 등 '목록형 게시판'을 URL 만으로 수집합니다.
- 기본(자동) 모드: 페이지에서 '링크 + 날짜'가 함께 있는 행(tr/li)을 찾아 공고로 인식
- 정밀 모드: CSS 선택자 지정 (list_selector / title_selector / date_selector)
- 자바스크립트로 그려지는 사이트: render: true (Playwright 필요)
미리보기:  python -m monitor probe <URL>
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..models import Notice
from .base import clean, find_dates, session

DEFAULT_FALLBACK_WORDS = ["공고", "공모", "모집", "선정", "제작", "의뢰"]


def fetch_html(source: dict) -> str:
    url = source["url"]
    if source.get("render"):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("render: true 는 playwright 설치가 필요합니다 (pip install playwright)") from e
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(locale="ko-KR")
            page.goto(url, wait_until="networkidle", timeout=45000)
            if source.get("wait_for"):
                page.wait_for_selector(source["wait_for"], timeout=15000)
            html = page.content()
            browser.close()
            return html

    r = session().request(
        source.get("method", "GET").upper(), url,
        params=source.get("params"), data=source.get("data"),
        headers=source.get("headers"), timeout=30,
    )
    r.raise_for_status()
    if source.get("encoding"):
        r.encoding = source["encoding"]
    elif not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r.text


def _best_anchor(row: Tag) -> Tag | None:
    anchors = [a for a in row.find_all("a") if len(clean(a.get_text())) >= 4 or a.get("title")]
    if not anchors:
        return None
    return max(anchors, key=lambda a: len(clean(a.get_text()) or a.get("title", "")))


def _resolve_link(a: Tag | None, row: Tag, source: dict, base_url: str) -> str:
    href = (a.get("href") if a else "") or ""
    onclick = " ".join(x.get("onclick", "") for x in (a, row) if x is not None)
    regex, template = source.get("link_regex"), source.get("link_template")
    if regex and template:
        m = re.search(regex, f"{href} {onclick}")
        if m:
            return template.format(*m.groups())
    if href and not href.startswith(("javascript", "#")):
        return urljoin(base_url, href)
    return base_url


def _rows_auto(soup: BeautifulSoup) -> list[Tag]:
    """링크와 날짜를 함께 가진 행이 가장 많은 후보 집합을 고른다."""
    best: list[Tag] = []
    for sel, tag in (("tr", "tr"), ("li", "li"), ("dl", "dl"),
                     ("div[class*=item]", "div"), ("div[class*=list] > div", "div")):
        rows = [r for r in soup.select(sel)
                if not r.find("th") and _best_anchor(r) is not None and find_dates(r.get_text(" "))]
        # 레이아웃 테이블처럼 다른 행을 품은 바깥 행은 제외 (가장 안쪽만)
        ids = {id(r) for r in rows}
        rows = [r for r in rows if not any(id(c) in ids for c in r.find_all(tag))]
        if len(rows) > len(best):
            best = rows
    return best


def extract(html: str, source: dict) -> list[Notice]:
    base_url = source.get("base_url") or source["url"]
    soup = BeautifulSoup(html, "html.parser")
    for bad in soup.select("script, style, header, footer, nav"):
        bad.decompose()

    if source.get("link_mode"):
        # 목록이 표 형태가 아닌 사이트: 주소에 특정 글자가 들어간 링크를 모두 게시글로 본다
        rows = [a for a in soup.find_all("a") if source.get("url_contains", "") in (a.get("href") or "")]
        fallback = True
    else:
        rows = soup.select(source["list_selector"]) if source.get("list_selector") else _rows_auto(soup)
        fallback = False
    if not rows and not source.get("list_selector") and not source.get("link_mode"):
        words = source.get("include") or DEFAULT_FALLBACK_WORDS
        rows = [a for a in soup.find_all("a") if any(w in clean(a.get_text()) for w in words)]
        fallback = True

    out: list[Notice] = []
    seen: set[str] = set()
    for row in rows:
        if fallback:
            a = row
        elif source.get("title_selector"):
            a = row.select_one(source["title_selector"])
        else:
            a = _best_anchor(row)
        if a is None:
            continue
        title = clean(a.get_text()) or clean(a.get("title"))
        title = re.sub(r"^(new|NEW|N)\s+|\s+(new|NEW|N)$", "", title)
        if source.get("title_strip"):                       # 제목에 섞여 들어온 날짜·조회수 등 제거
            title = clean(re.sub(source["title_strip"], "", title))
        if len(title) < 4:
            continue

        if source.get("date_selector"):
            d_el = row.select_one(source["date_selector"])
            dates = find_dates(d_el.get_text(" ") if d_el else "")
        else:
            dates = find_dates(row.get_text(" "))
        posted = dates[0] if dates else ""
        deadline = dates[-1] if len(dates) >= 2 and dates[-1] > posted else ""   # '게시일 ~ 마감일'

        if posted and source.get("max_age_days"):         # 너무 오래된 글은 제외
            from datetime import date, timedelta
            if posted < (date.today() - timedelta(days=int(source["max_age_days"]))).isoformat():
                continue

        url = _resolve_link(a, row, source, base_url)
        if source.get("url_contains") and source["url_contains"] not in url:
            continue                                     # 같은 화면의 다른 게시판(보도자료 등) 제외
        uid = url if url != base_url else f"{title}|{posted}"
        if uid in seen:
            continue
        seen.add(uid)
        out.append(Notice(
            source_id=source["id"], source_name=source.get("name", source["id"]),
            category=source.get("category", "제작 의뢰·공모"),
            title=title, url=url, uid=uid, org=source.get("org", ""),
            posted=posted, deadline=deadline,
        ))
        if len(out) >= int(source.get("max_items", 50)):
            break
    return out


_DEADLINE_WORDS = ("접수기간", "접수 기간", "공모기간", "응모기간", "모집기간", "접수마감", "마감일", "접수일정")


def detail_deadline(html: str) -> str:
    """상세 화면 글에서 '접수기간 2026.11.01 ~ 2026.12.15' 같은 부분을 찾아 마감일을 돌려준다."""
    text = BeautifulSoup(html, "html.parser").get_text(" ")
    for w in _DEADLINE_WORDS:
        i = text.find(w)
        if i >= 0:
            dates = find_dates(text[i:i + 80])
            if dates:
                return max(dates)
    return ""


def collect(source: dict, lookback_days: float | None = None) -> list[Notice]:
    notices = extract(fetch_html(source), source)
    if source.get("detail_deadline"):
        import time
        for n in notices[: int(source.get("max_details", 40))]:
            if n.url == source["url"]:
                continue
            try:
                r = session().get(n.url, timeout=30)
                if not r.encoding or r.encoding.lower() == "iso-8859-1":
                    r.encoding = r.apparent_encoding
                n.deadline = n.deadline or detail_deadline(r.text)
                if source.get("file_link_contains"):     # 상세 화면의 첨부파일 (공고문·서식 등)
                    dsoup = BeautifulSoup(r.text, "html.parser")
                    seen_f = set()
                    for fa in dsoup.find_all("a", href=True):
                        if source["file_link_contains"] in fa["href"] and fa["href"] not in seen_f:
                            seen_f.add(fa["href"])
                            n.files.append({"name": clean(fa.get_text()) or "첨부파일",
                                            "url": urljoin(n.url, fa["href"])})
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
    if not notices and not source.get("allow_empty"):
        raise RuntimeError("게시판에서 글을 하나도 찾지 못했습니다 (URL 또는 선택자 확인 필요)")
    return notices
