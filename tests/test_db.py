from datetime import datetime

import pytest

from brain.db import Db


@pytest.fixture
def db(tmp_path):
    return Db(tmp_path / "t.db")


def add(db, sid="s", turn=1, status="ok", cost=None, at=None, mj=None, tin=None, tout=None, audio=None):
    kw = dict(session_id=sid, turn=turn, question=f"q{turn}", status=status, cost_usd=cost,
              messages_json=mj, tokens_in=tin, tokens_out=tout, audio_file=audio)
    if at:
        kw["created_at"] = at
    return db.insert(**kw)


def test_next_turn(db):
    assert db.next_turn("s") == 1
    add(db, "s", 1)
    add(db, "s", 2)
    add(db, "other", 5)
    assert db.next_turn("s") == 3
    assert db.next_turn("other") == 6


def test_session_messages_only_ok_last_n_in_order(db):
    for t in range(1, 6):
        add(db, turn=t, mj=f"m{t}")
    add(db, turn=6, status="error", mj="bad")
    add(db, turn=7, status="limit", mj="bad2")
    add(db, turn=8, mj=None)  # ok без messages_json
    add(db, sid="x", turn=1, mj="чужая")
    assert db.session_messages("s", 3) == ["m3", "m4", "m5"]
    assert db.session_messages("s", 99) == ["m1", "m2", "m3", "m4", "m5"]
    assert db.session_messages("none", 3) == []


def test_cost_since(db):
    add(db, cost=0.1, at="2026-01-01T10:00:00")
    add(db, cost=0.2, at="2026-01-02T10:00:00")
    add(db, cost=None, at="2026-01-02T11:00:00")
    assert db.cost_since("2026-01-02") == pytest.approx(0.2)
    assert db.cost_since("2026-01-01") == pytest.approx(0.3)
    assert db.cost_since("2027-01-01") == 0.0


def test_stats_since_counts_only_ok(db):
    add(db, turn=1, cost=0.1, tin=10, tout=5, at="2026-01-02T10:00:00")
    add(db, turn=2, status="error", cost=0.9, at="2026-01-02T10:00:00")
    add(db, turn=3, cost=0.2, tin=1, tout=2, at="2026-01-03T10:00:00")
    s = db.stats_since("2026-01-02")
    assert s == {"requests": 2, "tokens_in": 11, "tokens_out": 7, "cost_usd": pytest.approx(0.3)}
    assert db.stats_since("2026-01-03")["requests"] == 1
    assert db.stats_since("")["requests"] == 2


def test_stats_since_respects_reset(db):
    add(db, turn=1, cost=0.1, at="2026-01-02T10:00:00")
    add(db, turn=2, cost=0.2, at="2026-01-02T12:00:00")
    db.set_meta("stats_reset_at", "2026-01-02T11:00:00")
    assert db.stats_since("2026-01-01")["requests"] == 1
    assert db.stats_since("2026-01-01")["cost_usd"] == pytest.approx(0.2)
    # since позже сброса — берётся since
    assert db.stats_since("2026-01-05")["requests"] == 0
    # история и cost_since сброс не затрагивает
    assert db.cost_since("2026-01-01") == pytest.approx(0.3)
    assert len(db.history(10)) == 2


def test_reset_stats_sets_meta(db):
    now = db.reset_stats()
    assert db.get_meta("stats_reset_at") == now
    datetime.fromisoformat(now)
    assert db.get_meta("nope") is None


def test_history_limit_and_order(db):
    for t in range(1, 6):
        add(db, turn=t)
    h = db.history(3)
    assert [r["turn"] for r in h] == [5, 4, 3]
    assert "messages_json" not in h[0]
    assert len(db.history(100)) == 5


def test_clear_audio(db):
    add(db, turn=1, audio="a.wav")
    add(db, turn=2, audio="b.wav")
    db.clear_audio("a.wav")
    rows = {r["turn"]: r["audio_file"] for r in db.history(10)}
    assert rows == {1: None, 2: "b.wav"}
