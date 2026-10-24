"""Хранилище сессий: SQLite через SQLAlchemy (PostgreSQL — сменой DATABASE_URL).

Состояние графа (профиль, подборка, корзина) лежит JSON-ом по session_id,
события — отдельной таблицей для метрик.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from sqlalchemy import Column, Float, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.pool import StaticPool

DB_URL = os.getenv("DATABASE_URL", f"sqlite:///{Path(__file__).resolve().parent.parent / 'odin.db'}")
if ":memory:" in DB_URL:  # тесты: одна общая in-memory БД на все потоки
    engine = create_engine(DB_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
else:
    engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {})


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"
    id = Column(String, primary_key=True)
    state = Column(Text, nullable=False)
    created_at = Column(Float, default=time.time)
    updated_at = Column(Float, default=time.time)


class EventRow(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, index=True)
    type = Column(String)
    payload = Column(Text)
    ts = Column(Float, default=time.time)


Base.metadata.create_all(engine)

_mem: dict[str, dict] = {}
_lock = threading.Lock()


def save(state: dict) -> None:
    with _lock:
        _mem[state["session_id"]] = state
        data = json.dumps(state, ensure_ascii=False, default=str)
        with Session(engine) as s:
            row = s.get(SessionRow, state["session_id"])
            if row:
                row.state, row.updated_at = data, time.time()
            else:
                s.add(SessionRow(id=state["session_id"], state=data))
            s.commit()


def load(session_id: str) -> dict | None:
    if session_id in _mem:
        return _mem[session_id]
    with Session(engine) as s:
        row = s.get(SessionRow, session_id)
        if not row:
            return None
        st = json.loads(row.state)
        _mem[session_id] = st
        return st


def event(session_id: str, type_: str, payload: dict | None = None) -> None:
    with Session(engine) as s:
        s.add(EventRow(session_id=session_id, type=type_, payload=json.dumps(payload or {}, ensure_ascii=False, default=str)))
        s.commit()


def all_states(limit: int = 500) -> list[dict]:
    with Session(engine) as s:
        rows = s.execute(select(SessionRow).order_by(SessionRow.updated_at.desc()).limit(limit)).scalars().all()
        return [json.loads(r.state) for r in rows]
