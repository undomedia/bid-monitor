"""검색 기반 수집기: 입찰 외에 올라오는 '영상 제작 의뢰/업체 모집' 글을 찾는다.

naver  — 네이버 검색 API (카페글·블로그·뉴스·지식iN). https://developers.naver.com 에서
         애플리케이션 등록(검색 API) 후 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수 설정.
gnews  — 구글 뉴스 RSS. 키 없이 동작.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import quote

from ..models import Notice
from .base import clean, session

NAVER_TYPES = {"cafe": "cafearticle", "blog": "blog", "news": "news", "kin": "kin"}
NAVER_LABEL = {"cafe": "네이버 카페", "blog": "네이버 블로그", "news": "뉴스", "kin": "지식iN"}


def _strip_tags(s: str) -> str:
    return clean(unescape(re.sub(r"<[^>]+>", "", s or "")))


def _naver_date(item: dict) -> str:
    if item.get("postdate"):                      # blog: '20261111'
        d = item["postdate"]
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    if item.get("pubDate"):                       # news: RFC822
        try:
            return parsedate_to_datetime(item["pubDate"]).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            pass
    return ""                                     # 카페글은 날짜를 주지 않음


def collect_naver(source: dict, lookback_days: float | None = None) -> list[Notice]:
    cid, secret = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and secret):
        raise RuntimeError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 없습니다")
    days = lookback_days if lookback_days is not None else float(source.get("lookback_days", 14))
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    headers = {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret}

    out: list[Notice] = []
    for kind in source.get("targets", ["cafe", "blog", "news"]):
        for q in source["queries"]:
            r = session().get(
                f"https://openapi.naver.com/v1/search/{NAVER_TYPES[kind]}.json",
                params={"query": q, "display": int(source.get("display", 30)), "sort": "date"},
                headers=headers, timeout=20,
            )
            r.raise_for_status()
            for it in r.json().get("items", []):
                posted = _naver_date(it)
                if posted and posted < since:
                    continue
                url = it.get("originallink") or it.get("link", "")
                out.append(Notice(
                    source_id=source["id"], source_name=NAVER_LABEL[kind],
                    category=source.get("category", "제작 의뢰·공모"),
                    title=_strip_tags(it.get("title")), url=url, uid=url,
                    org=clean(it.get("cafename") or it.get("bloggername") or ""),
                    posted=posted, kind=_strip_tags(it.get("description"))[:120],
                ))
    return out


def collect_gnews(source: dict, lookback_days: float | None = None) -> list[Notice]:
    days = lookback_days if lookback_days is not None else float(source.get("lookback_days", 14))
    out: list[Notice] = []
    for q in source["queries"]:
        url = (f"https://news.google.com/rss/search?q={quote(q)}+when:{max(1, int(days))}d"
               "&hl=ko&gl=KR&ceid=KR:ko")
        r = session().get(url, timeout=20)
        r.raise_for_status()
        out.extend(parse_rss(r.content, source))
    return dedupe_similar(out)


def dedupe_similar(items: list[Notice]) -> list[Notice]:
    """여러 언론사가 같은 보도자료를 실은 경우 하나만 남긴다 (제목 앞부분이 같으면 중복)."""
    seen, out = set(), []
    for n in items:
        key = re.sub(r"[^가-힣A-Za-z0-9]", "", n.title)[:14]
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


def parse_rss(xml_bytes: bytes, source: dict) -> list[Notice]:
    out = []
    root = ET.fromstring(xml_bytes)
    for it in root.iter("item"):
        title = clean(it.findtext("title"))
        link = clean(it.findtext("link"))
        org = clean(it.findtext("source"))
        if org and title.endswith(f" - {org}"):
            title = title[: -len(org) - 3]
        posted = ""
        try:
            posted = parsedate_to_datetime(it.findtext("pubDate") or "").strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            pass
        out.append(Notice(
            source_id=source["id"], source_name=source.get("name", "뉴스"),
            category=source.get("category", "제작 의뢰·공모"),
            title=title, url=link, uid=link, org=org, posted=posted,
        ))
    return out
