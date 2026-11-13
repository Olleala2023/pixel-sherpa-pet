"""История запросов в SQLite (brain/data/brain.db)."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id             INTEGER PRIMARY KEY,
  created_at     TEXT    NOT NULL,
  session_id     TEXT    NOT NULL,
  turn           INTEGER NOT NULL,
  source         TEXT    NOT NULL DEFAULT 'pet',
  audio_file     TEXT,
  audio_sec      REAL,
  transcript_raw TEXT,
  question       TEXT    NOT NULL,
  answer         TEXT,
  model          TEXT,
  tokens_in      INTEGER,
  tokens_out     INTEGER,
  cost_usd       REAL,
  stt_ms         INTEGER,
  llm_ms         INTEGER,
  status         TEXT    NOT NULL,
  error          TEXT,
  messages_json  TEXT
);
CREATE INDEX IF NOT EXISTS requests_created ON requests(created_at);
CREATE INDEX IF NOT EXISTS requests_session ON requests(session_id, turn);
"""

PUBLIC_COLS = (
    "id, created_at, session_id, turn, source, audio_file, audio_sec, transcript_raw, question, answer, "
    "model, tokens_in, tokens_out, cost_usd, stt_ms, llm_ms, status, error"
)


class Db:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def insert(self, **row) -> int:
        row.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
        cols = ", ".join(row)
        qs = ", ".join("?" for _ in row)
        cur = self.conn.execute(f"INSERT INTO requests ({cols}) VALUES ({qs})", tuple(row.values()))
        return int(cur.lastrowid)

    def next_turn(self, session_id: str) -> int:
        r = self.conn.execute("SELECT COALESCE(MAX(turn), 0) FROM requests WHERE session_id = ?", (session_id,)).fetchone()
        return int(r[0]) + 1

    def session_messages(self, session_id: str, max_pairs: int) -> list[str]:
        """messages_json успешных ходов сессии, последние max_pairs, по порядку."""
        rows = self.conn.execute(
            "SELECT messages_json FROM requests WHERE session_id = ? AND status = 'ok' AND messages_json IS NOT NULL "
            "ORDER BY turn DESC LIMIT ?",
            (session_id, max_pairs),
        ).fetchall()
        return [r[0] for r in reversed(rows)]

    def cost_since(self, since_iso: str) -> float:
        r = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM requests WHERE created_at >= ?", (since_iso,)
        ).fetchone()
        return float(r[0])

    def stats_since(self, since_iso: str) -> dict:
        r = self.conn.execute(
            "SELECT COUNT(*) AS requests, COALESCE(SUM(tokens_in), 0) AS tokens_in, "
            "COALESCE(SUM(tokens_out), 0) AS tokens_out, COALESCE(SUM(cost_usd), 0) AS cost_usd "
            "FROM requests WHERE created_at >= ? AND status = 'ok'",
            (since_iso,),
        ).fetchone()
        return dict(r)

    def history(self, limit: int) -> list[dict]:
        rows = self.conn.execute(f"SELECT {PUBLIC_COLS} FROM requests ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def clear_audio(self, audio_file: str) -> None:
        self.conn.execute("UPDATE requests SET audio_file = NULL WHERE audio_file = ?", (audio_file,))
