"""사이트 점검: 방송사 공모 게시판 후보를 열어 보고 무엇이 보이는지 기록한다.

GitHub Actions(인터넷이 열린 환경)에서 실행해 결과를 로그와 probe_report.md 로 남긴다.
  python tools/probe_sites.py tools/probe_urls.txt
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup  # noqa: E402

from monitor.collectors import board  # noqa: E402
from monitor.collectors.base import clean, session  # noqa: E402

LINK_WORDS = re.compile(r"공지|공고|공모|외주|제작사|협력|상생|기획안|입찰|알림|notice|board|bbs", re.I)


class _Rendered:
    def __init__(self, url, html):
        self.url, self.text, self.status_code, self.encoding = url, html, 200, "utf-8"


def probe(url: str) -> str:
    render = url.startswith("render ")
    url = url.removeprefix("render ").strip()
    out = [f"\n## {url}{' (브라우저 렌더링)' if render else ''}"]
    try:
        if render:
            src = {"url": url, "render": True}
            r = _Rendered(url, board.fetch_html(src))
        else:
            r = session().get(url, timeout=30)
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding
        html = r.text
        # 화면 원본도 저장 (점검 결과와 함께 내려받아 구조 확인용)
        import hashlib
        Path("probe_html").mkdir(exist_ok=True)
        Path("probe_html", hashlib.md5(url.encode()).hexdigest()[:8] + ".html").write_text(
            f"<!-- {url} -->\n" + html, encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        title = clean(soup.title.get_text()) if soup.title else ""
        out.append(f"- 상태 {r.status_code}, 최종주소 {r.url}, 크기 {len(html):,}, 제목 '{title}'")
    except Exception as e:  # noqa: BLE001
        out.append(f"- 실패: {type(e).__name__}: {e}")
        return "\n".join(out)

    # 화면 글 중 공고처럼 보이는 줄 (게시글이 링크가 아닌 사이트 구조 확인용)
    lines = [clean(x) for x in soup.get_text("\n").split("\n")]
    hits = [x for x in lines if x and len(x) < 160 and re.search(r"공모|공고|모집|20\d\d[.-]|End Point|apis\.data\.go\.kr|^/[A-Za-z-]+$|상세기능|입찰", x)]
    if hits:
        out.append(f"- 화면 글 중 공고 후보 {len(hits)}줄")
        out.extend(f"    · {x}" for x in hits[:30])
    clicks = [clean(str(t))[:160] for t in soup.select("[onclick]")][:8]
    if clicks:
        out.append("- 클릭 동작 예: " + " / ".join(clicks))
    from monitor.collectors.board import detail_deadline
    dl = detail_deadline(html)
    if dl:
        out.append(f"- 접수 마감일 인식: {dl}")
    items = board.extract(html, {"id": "p", "url": r.url})
    out.append(f"- 자동 인식 게시글 {len(items)}건")
    for n in items[:12]:
        out.append(f"  - {n.posted or '----------'} | {n.title[:70]} | {n.url[:120]}")

    links, seen = [], set()
    for a in soup.find_all("a"):
        text = clean(a.get_text()) or clean(a.get("title"))
        href = a.get("href") or ""
        onclick = a.get("onclick") or ""
        if not text or len(text) > 60:
            continue
        if LINK_WORDS.search(text) or LINK_WORDS.search(href):
            key = (text, href)
            if key in seen:
                continue
            seen.add(key)
            target = urljoin(r.url, href) if href and not href.startswith("javascript") else (href + " " + onclick)
            links.append(f"  - [{text}] {target[:160]}")
    out.append(f"- 관련 링크 {len(links)}개")
    out.extend(links[:40])
    scripts = re.findall(r"""['"]([^'"]*(?:list|List|bbs|board|notice)[^'"]*\.(?:do|php|jsp|json)[^'"]*)['"]""", html)
    if scripts:
        out.append("- 페이지 안 목록 주소 후보: " + ", ".join(sorted(set(scripts))[:15]))
    return "\n".join(out)


def main() -> None:
    urls = [l.strip() for l in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]
    report = "# 사이트 점검 결과\n" + "\n".join(probe(u) for u in urls)
    Path("probe_report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
