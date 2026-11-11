"""영상 관련 키워드 필터."""
from __future__ import annotations

from .models import Notice


def _norm(s: str) -> str:
    return (s or "").replace(" ", "").lower()


def match(text: str, keywords: list[str]) -> list[str]:
    t = _norm(text)
    return [k for k in keywords if _norm(k) and _norm(k) in t]


def apply(notices: list[Notice], source: dict, settings: dict) -> list[Notice]:
    """제목에 영상 관련 키워드가 있고, 제외 키워드가 없는 것만 남긴다.

    source.filter: keywords(기본) | all (게시판 자체가 영상 공모 전용일 때)
    source.exclude: 이 소스에만 추가로 적용할 제외 키워드
    """
    keywords = source.get("keywords") or settings.get("keywords", [])
    excludes = settings.get("exclude_keywords", []) + source.get("exclude", [])
    mode = source.get("filter", "keywords")

    out = []
    strong = settings.get("strong_keywords", [])
    hard = ["CCTV", "폐쇄회로", "영상정보처리"]   # 이건 영상제작과 같이 있어도 제외
    for n in notices:
        if not n.title:
            continue
        if match(n.title, excludes) and (match(n.title, hard) or not match(n.title, strong)):
            continue
        n.matched = match(n.title, keywords)
        if mode == "keywords" and not n.matched:
            continue
        out.append(n)
    return out
