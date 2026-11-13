"""SQLite 저장소: 중복 제거 및 신규 공고 판별."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .models import Notice

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    id          TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    data        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notices_seen ON notices(first_seen);
CREATE TABLE IF NOT EXISTS analysis (
    id          TEXT PRIMARY KEY,
    done        INTEGER NOT NULL,
    data        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_status (
    source_id   TEXT PRIMARY KEY,
    data        TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)

    def has_source(self, source_id: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM notices WHERE source_id=? LIMIT 1", (source_id,))
        return cur.fetchone() is not None

    def add(self, notices: list[Notice], now: str) -> list[Notice]:
        """저장하고, 이번에 처음 본 공고만 반환."""
        new: list[Notice] = []
        seen_ids: set[str] = set()
        for n in notices:
            if n.id in seen_ids:
                continue
            seen_ids.add(n.id)
            n.first_seen = now
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO notices(id, source_id, first_seen, data) VALUES (?,?,?,?)",
                (n.id, n.source_id, now, json.dumps(n.to_dict(), ensure_ascii=False)),
            )
            if cur.rowcount:
                new.append(n)
            elif n.files or n.info or n.deadline:
                # 이미 있는 공고: 처음 본 시각은 유지하고 첨부파일·상세정보만 최신으로
                row = self.conn.execute("SELECT data FROM notices WHERE id=?", (n.id,)).fetchone()
                if row:
                    old = json.loads(row[0])
                    if (old.get("files") != n.files or old.get("info") != n.info
                            or (n.deadline and old.get("deadline") != n.deadline)):
                        old["files"], old["info"] = n.files or old.get("files", []), n.info or old.get("info", {})
                        old["deadline"] = n.deadline or old.get("deadline", "")
                        self.conn.execute("UPDATE notices SET data=? WHERE id=?",
                                          (json.dumps(old, ensure_ascii=False), n.id))
        self.conn.commit()
        return new

    def recent(self, days: int, with_analysis: bool = False) -> list[dict]:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        cur = self.conn.execute(
            "SELECT data FROM notices WHERE first_seen >= ? ORDER BY first_seen DESC", (since,)
        )
        rows = [json.loads(r[0]) for r in cur.fetchall()]
        if with_analysis:
            an = self.analyses()
            for n in rows:
                if n["id"] in an:
                    n["analysis"] = an[n["id"]]
        return rows

    # ----- AI 분석 결과 -----
    def analyses(self) -> dict[str, dict]:
        cur = self.conn.execute("SELECT id, data FROM analysis")
        return {i: json.loads(d) for i, d in cur.fetchall()}

    def analyzed_ids(self) -> set[str]:
        """성공했거나, 여러 번 실패해 포기한 공고."""
        return {r[0] for r in self.conn.execute("SELECT id FROM analysis WHERE done=1")}

    def set_analysis(self, notice_id: str, data: dict, final: bool | None = None) -> None:
        done = data.get("ok", False) if final is None else (final or data.get("ok", False))
        self.conn.execute("INSERT OR REPLACE INTO analysis(id, done, data) VALUES (?,?,?)",
                          (notice_id, int(done), json.dumps(data, ensure_ascii=False)))
        self.conn.commit()

    def delete(self, ids: list[str]) -> None:
        self.conn.executemany("DELETE FROM notices WHERE id=?", [(i,) for i in ids])
        self.conn.executemany("DELETE FROM analysis WHERE id=?", [(i,) for i in ids])
        self.conn.commit()

    def prune(self, keep_days: int) -> int:
        cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        cur = self.conn.execute("DELETE FROM notices WHERE first_seen < ?", (cutoff,))
        self.conn.execute("DELETE FROM analysis WHERE id NOT IN (SELECT id FROM notices)")
        self.conn.commit()
        return cur.rowcount

    def set_status(self, source_id: str, status: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO source_status(source_id, data) VALUES (?,?)",
            (source_id, json.dumps(status, ensure_ascii=False)),
        )
        self.conn.commit()

    def statuses(self) -> dict[str, dict]:
        cur = self.conn.execute("SELECT source_id, data FROM source_status")
        return {sid: json.loads(d) for sid, d in cur.fetchall()}
