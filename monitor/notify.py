"""새 공고 알림: 텔레그램 / 이메일."""
from __future__ import annotations

import html
import os
import smtplib
from collections import defaultdict
from email.mime.text import MIMEText

from .collectors.base import session
from .models import Notice


def _won(amount: int | None) -> str:
    if not amount:
        return ""
    if amount >= 100_000_000:
        return f"{amount / 100_000_000:.1f}억원"
    return f"{amount // 10_000:,}만원"


def _line_html(n: Notice) -> str:
    meta = [x for x in (n.org, n.kind, _won(n.amount)) if x]
    if n.deadline:
        meta.append(f"마감 {n.deadline[5:]}")
    star = "⭐ " if n.matched else ""
    return (f'• {star}<a href="{html.escape(n.url, quote=True)}">{html.escape(n.title)}</a>'
            + (f"\n   <i>{html.escape(' · '.join(meta))}</i>" if meta else ""))


def build_messages(new: list[Notice], dashboard_url: str = "", limit: int = 4000) -> list[str]:
    """카테고리별로 묶은 텔레그램 HTML 메시지 (4096자 제한에 맞춰 분할)."""
    groups: dict[str, list[Notice]] = defaultdict(list)
    for n in new:
        groups[n.category].append(n)
    # 관심 키워드 매칭 공고를 먼저
    head = f"📢 <b>새 공고 {len(new)}건</b>"
    if dashboard_url:
        head += f'  <a href="{html.escape(dashboard_url)}">대시보드 열기</a>'
    blocks = [head]
    for cat, items in groups.items():
        items.sort(key=lambda n: (not n.matched, n.deadline or "9999"))
        blocks.append(f"\n<b>[{html.escape(cat)}] {len(items)}건</b>")
        blocks.extend(_line_html(n) for n in items)

    messages, cur = [], ""
    for b in blocks:
        if len(cur) + len(b) + 1 > limit:
            messages.append(cur)
            cur = ""
        cur += b + "\n"
    if cur.strip():
        messages.append(cur)
    return messages


def send_telegram(new: list[Notice], dashboard_url: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return
    for text in build_messages(new, dashboard_url):
        r = session().post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": "true"},
            timeout=20,
        )
        r.raise_for_status()


def send_email(new: list[Notice], dashboard_url: str) -> None:
    host, user, pw, to = (os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_TO"))
    if not (host and user and pw and to):
        return
    body = "<br>".join(m.replace("\n", "<br>") for m in build_messages(new, dashboard_url, limit=10**9))
    msg = MIMEText(f"<div style='font-family:sans-serif;line-height:1.6'>{body}</div>", "html", "utf-8")
    msg["Subject"] = f"[공고 알림] 새 공고 {len(new)}건"
    msg["From"] = user
    msg["To"] = to
    with smtplib.SMTP_SSL(host, int(os.environ.get("SMTP_PORT", 465)), timeout=30) as s:
        s.login(user, pw)
        s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())


def notify(new: list[Notice], settings: dict) -> list[str]:
    """알림 전송. 실패한 채널 이름 목록 반환."""
    ncfg = settings.get("notify", {})
    targets = new
    if ncfg.get("only_matched"):
        # 키워드 필터가 없는 소스(방송사 공모 등)는 항상 포함
        targets = [n for n in new if n.matched or n.category in ncfg.get("always_categories", [])]
    if not targets:
        return []
    url = os.environ.get("DASHBOARD_URL") or settings.get("dashboard_url", "")
    errors = []
    for name, fn in (("telegram", send_telegram), ("email", send_email)):
        try:
            fn(targets, url)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
    return errors
