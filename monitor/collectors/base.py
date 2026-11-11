"""수집기 공통 유틸."""
from __future__ import annotations

import re
from datetime import date

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"})
    return _session


# 2026-11-11, 2026.11.11, 2026/11/11, 2026. 11. 11, 26.11.11, 2026년 11월 11일
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2}|\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})(?!\d)"
)


def find_dates(text: str) -> list[str]:
    """문자열에서 날짜들을 찾아 YYYY-MM-DD 로 반환."""
    out = []
    for y, m, d in _DATE_RE.findall(text or ""):
        year = int(y) + 2000 if len(y) == 2 else int(y)
        try:
            out.append(date(year, int(m), int(d)).isoformat())
        except ValueError:
            continue
    return out


def parse_date(text: str) -> str:
    dates = find_dates(text)
    return dates[0] if dates else ""


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()
