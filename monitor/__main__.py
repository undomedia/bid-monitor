"""실행: python -m monitor <명령>

  run              모든 소스 수집 → 저장 → 대시보드 생성 → 새 공고 알림
  run --only ID    특정 소스만 수집
  run --no-notify  알림 없이 수집만
  run --days 60    조회 기간을 늘려서 실행 (처음 설치 시 과거 공고 채우기)
  probe URL        게시판 URL 에서 자동 인식되는 공고 미리보기 (소스 추가 전 확인용)
  build            저장된 데이터로 대시보드만 다시 생성
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import yaml

from . import analyze, filters, notify
from .collectors import COLLECTORS
from .collectors import board
from .db import Store

ROOT = Path(__file__).resolve().parent.parent

_SECRET_RE = re.compile(r"((?:serviceKey|servicekey|ServiceKey|key|token|apikey|api_key|client_secret)=)[^&\s'\"]+", re.I)


def redact(text: str) -> str:
    """오류 메시지에 들어간 인증키·토큰을 가린다 (대시보드는 공개되므로)."""
    text = _SECRET_RE.sub(r"\1***", text)
    for env in ("G2B_SERVICE_KEY", "ANTHROPIC_API_KEY", "NAVER_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "SMTP_PASSWORD"):
        v = os.environ.get(env)
        if v and len(v) >= 8:
            text = text.replace(v, "***")
    return text


def load_config(path: Path) -> tuple[dict, list[dict]]:
    settings = yaml.safe_load((path / "settings.yaml").read_text(encoding="utf-8")) or {}
    sources = yaml.safe_load((path / "sources.yaml").read_text(encoding="utf-8")) or {}
    return settings, [s for s in sources.get("sources", []) if s.get("enabled", True)]


def cleanup(store: Store, settings: dict, sources: list[dict]) -> None:
    """제외어를 바꾸면 이미 저장된 공고에도 적용 (대시보드에서 바로 사라지게)."""
    from .models import Notice
    by_id = {x["id"]: x for x in sources}
    gone = []
    for d in store.recent(int(settings.get("keep_days", 180))):
        src = by_id.get(d["source_id"], {})
        n = Notice(source_id=d["source_id"], source_name="", category="", title=d["title"], url="")
        if not filters.apply([n], src, settings):
            gone.append(d["id"])
    if gone:
        store.delete(gone)
        print(f"바뀐 제외어에 따라 기존 공고 {len(gone)}건 정리")


def build_site(store: Store, settings: dict, sources: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    statuses = store.statuses()
    data = {
        "generated": max((st.get("checked", "") for st in statuses.values()), default="")
                     or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "keywords": settings.get("keywords", []),
        "highlight": settings.get("highlight_keywords", []),
        "sources": [
            {"id": s["id"], "name": s.get("name", s["id"]), "category": s.get("category", ""),
             "url": s.get("home") or s.get("url", ""), **statuses.get(s["id"], {})}
            for s in sources
        ],
        "notices": store.recent(int(settings.get("dashboard_days", 60)), with_analysis=True),
    }
    (out_dir / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for f in (ROOT / "web").iterdir():          # index.html, 아이콘, manifest
        if f.is_file():
            shutil.copy(f, out_dir / f.name)


def cmd_run(args, settings, sources) -> int:
    store = Store(ROOT / settings.get("db_path", "data/notices.db"))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    all_new = []
    for src in sources:
        if args.only and src["id"] not in args.only:
            continue
        fn = COLLECTORS.get(src.get("type", "board"))
        if fn is None:
            print(f"[skip] {src['id']}: 알 수 없는 type {src.get('type')}")
            continue
        t0 = time.time()
        first_run = not store.has_source(src["id"])
        try:
            items = filters.apply(fn(src, args.days), src, settings)
            new = store.add(items, now)
            status = {"ok": True, "checked": now, "found": len(items), "new": len(new), "error": ""}
            # 처음 등록한 소스는 기존 공고가 한꺼번에 알림으로 가지 않도록 조용히 저장만
            if not first_run:
                all_new.extend(new)
            print(f"[ok]   {src['id']:<24} 수집 {len(items):>4}건 / 신규 {len(new):>3}건"
                  f"{' (첫 수집: 알림 생략)' if first_run and new else ''} ({time.time()-t0:.1f}s)")
        except Exception as e:  # noqa: BLE001 — 한 사이트 실패가 전체를 막지 않도록
            prev = store.statuses().get(src["id"], {})
            status = {"ok": False, "checked": now, "found": 0, "new": 0,
                      "error": redact(f"{type(e).__name__}: {e}")[:300],
                      "last_ok": prev.get("checked") if prev.get("ok") else prev.get("last_ok", "")}
            print(f"[FAIL] {src['id']:<24} {status['error']}")
            if args.verbose:
                traceback.print_exc()
        if status["ok"]:
            status["last_ok"] = now
        store.set_status(src["id"], status)

    store.prune(int(settings.get("keep_days", 180)))
    cleanup(store, settings, sources)
    if not args.no_analyze:
        analyze.run(store, settings, now)
    build_site(store, settings, sources, ROOT / settings.get("site_dir", "docs"))
    print(f"\n신규 공고 총 {len(all_new)}건, 대시보드 생성 완료")

    if all_new and not args.no_notify:
        for err in notify.notify(all_new, settings):
            print(f"[알림 실패] {redact(err)}")
    return 0


def cmd_probe(args) -> int:
    src = {"id": "probe", "name": "probe", "url": args.url, "render": args.render}
    if args.list_selector:
        src["list_selector"] = args.list_selector
    if args.encoding:
        src["encoding"] = args.encoding
    items = board.extract(board.fetch_html(src), src)
    if not items:
        print("공고를 찾지 못했습니다. --render 옵션(자바스크립트 사이트)이나 --list-selector 를 시도해 보세요.")
        return 1
    for n in items:
        print(f"{n.posted or '----------'}  {n.deadline or '':<10}  {n.title[:60]}\n{'':12}{n.url}")
    print(f"\n총 {len(items)}건 인식됨")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m monitor", description="영상제작 입찰·의뢰 모니터")
    p.add_argument("--config", default=str(ROOT / "config"))
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="수집 + 대시보드 + 알림")
    r.add_argument("--only", nargs="*", help="특정 소스 ID만 실행")
    r.add_argument("--no-notify", action="store_true")
    r.add_argument("--no-analyze", action="store_true", help="제안요청서 AI 분석 생략")
    r.add_argument("--days", type=float, help="조회 기간(일) 일시 변경. 처음 설치 시 --days 60 으로 과거 공고 채우기")
    r.add_argument("-v", "--verbose", action="store_true")
    b = sub.add_parser("probe", help="게시판 URL 자동 인식 미리보기")
    b.add_argument("url")
    b.add_argument("--render", action="store_true", help="브라우저로 렌더링 (JS 사이트)")
    b.add_argument("--list-selector")
    b.add_argument("--encoding")
    sub.add_parser("build", help="대시보드만 다시 생성")
    args = p.parse_args(argv)

    if args.cmd == "probe":
        return cmd_probe(args)
    settings, sources = load_config(Path(args.config))
    if args.cmd == "build":
        store = Store(ROOT / settings.get("db_path", "data/notices.db"))
        cleanup(store, settings, sources)
        build_site(store, settings, sources, ROOT / settings.get("site_dir", "docs"))
        return 0
    return cmd_run(args, settings, sources)


if __name__ == "__main__":
    sys.exit(main())
