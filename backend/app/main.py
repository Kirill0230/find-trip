"""REST API на FastAPI — договор из ТЗ. Долгие ответы (подборка, пакет) идут SSE."""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from collections import defaultdict
from contextvars import copy_context
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import store
from .agent import flows, llm
from .agent.flows import BookingConflict
from .agent.memory import remember
from .metrics import summary

app = FastAPI(title="О!дин запрос — прототип агента", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


def _state(sid: str) -> dict:
    st = store.load(sid)
    if not st:
        raise HTTPException(404, "session not found")
    return st


def _remember(st: dict) -> None:
    if st.get("user_id"):
        store.save_user(st["user_id"], remember(st, store.load_user(st["user_id"])))


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


class SessionIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=64)


class SwipeIn(BaseModel):
    video_id: str
    direction: Literal["right", "left", "watched"]


class CorrectIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    context: Literal["feed", "shortlist", "package", "checkout", "clarify"] = "feed"


class PackageIn(BaseModel):
    hotel_id: str
    room_category: str | None = None


class PackagePatch(BaseModel):
    adults: int | None = Field(default=None, ge=1, le=9)
    children_ages: list[int] | None = None
    date_from: str | None = None
    date_to: str | None = None
    origin_city: str | None = None
    flight_pref: Literal["cheap", "fast", "optimal"] | None = None
    outbound_id: str | None = None
    return_id: str | None = None
    arrival_type: str | None = None
    departure_type: str | None = None
    room_category: str | None = None
    anything_else: str | None = None


class Guest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class BookIn(BaseModel):
    guests: list[Guest]
    email: str = ""
    phone: str = ""


@app.get("/health")
def health():
    return {"ok": True, "llm": llm.enabled(), "offline": os.getenv("OFFLINE") == "1"}


@app.post("/session")
def create_session(body: SessionIn):
    sid = "s_" + uuid.uuid4().hex[:10]
    t0 = time.perf_counter()
    memory = store.load_user(body.user_id) if body.user_id else None
    st = flows.intake(sid, body.text, memory=memory, user_id=body.user_id)
    store.save(st)
    store.event(sid, "intake", {"ms": round((time.perf_counter() - t0) * 1000), "mode": st["mode"]})
    view = flows.session_view(st)
    view["mode"] = st["mode"] if st["mode"] != "feed" else "feed"
    return view


@app.get("/session/{sid}")
def get_session(sid: str):
    return flows.session_view(_state(sid))


@app.get("/session/{sid}/feed")
def feed(sid: str, n: int = Query(3, ge=1, le=10)):
    with _locks[sid]:
        st = _state(sid)
        res = flows.get_feed(st, n)
        store.save(st)
    return res


@app.post("/session/{sid}/swipe")
def swipe(sid: str, body: SwipeIn):
    with _locks[sid]:
        st = _state(sid)
        if body.video_id not in st["served"]:
            raise HTTPException(400, "unknown video for this session")
        res = flows.swipe(st, body.video_id, body.direction)
        store.save(st)
    store.event(sid, "swipe", body.model_dump())
    return res


@app.get("/session/{sid}/shortlist")
def shortlist(sid: str, stream: int = 1):
    with _locks[sid]:
        st = _state(sid)
        sl = flows.build_shortlist(st)
        flows.mark_shortlist_shown(st)
        store.save(st)
        _remember(st)
    if not stream:
        return sl

    def gen():
        yield _sse("status", {"text": f"Сравниваю {len(sl['items'])} варианта по вашим приоритетам"})
        yield _sse("priorities", sl["priorities"])
        for it in sl["items"]:
            yield _sse("item", it)
        if sl["removed"]:
            yield _sse("removed", sl["removed"])
        yield _sse("done", {"count": len(sl["items"])})
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/session/{sid}/correct")
def correct(sid: str, body: CorrectIn):
    with _locks[sid]:
        st = _state(sid)
        res = flows.correct(st, body.text, body.context)
        store.save(st)
    store.event(sid, "correct", {"text": body.text, "type": res["type"]})
    return res


@app.post("/session/{sid}/package")
def package(sid: str, body: PackageIn, stream: int = 1):
    st = _state(sid)
    if not stream:
        with _locks[sid]:
            pkg = flows.create_package(st, body.hotel_id, body.room_category)
            store.save(st)
        return pkg
    q: queue.Queue = queue.Queue()

    def work():
        try:
            with _locks[sid]:
                pkg = flows.create_package(st, body.hotel_id, body.room_category, on_step=lambda name, p: q.put(("status", {"step": name, **p})))
                store.save(st)
            q.put(("package", pkg))
        except Exception as e:  # отдаём ошибку в поток, а не рвём соединение
            q.put(("error", {"message": str(e)}))
        q.put(None)

    threading.Thread(target=copy_context().run, args=(work,), daemon=True).start()

    def gen():
        while True:
            item = q.get()
            if item is None:
                break
            yield _sse(*item)
        yield _sse("done", {})
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.patch("/session/{sid}/package")
def patch_package(sid: str, body: PackagePatch):
    with _locks[sid]:
        st = _state(sid)
        if not st.get("package"):
            raise HTTPException(400, "no package yet")
        pkg = flows.patch_package(st, body.model_dump(exclude_none=True))
        store.save(st)
    return pkg


@app.post("/session/{sid}/book")
def book(sid: str, body: BookIn):
    with _locks[sid]:
        st = _state(sid)
        if not st.get("package"):
            raise HTTPException(400, "no package yet")
        try:
            order = flows.book(st, [g.model_dump() for g in body.guests], {"email": body.email, "phone": body.phone})
        except BookingConflict as e:
            store.save(st)
            return JSONResponse(status_code=409, content={"error": "conflicts", "conflicts": e.conflicts, "package": st["package"]})
        store.save(st)
        _remember(st)
    store.event(sid, "book", {"order_id": order["order_id"]})
    return order


class OrderRequestIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


def _order_state(sid: str) -> dict:
    st = _state(sid)
    if not st.get("order"):
        raise HTTPException(400, "no order yet")
    return st


@app.post("/session/{sid}/order/request")
def order_request(sid: str, body: OrderRequestIn):
    with _locks[sid]:
        st = _order_state(sid)
        res = flows.order_request(st, body.text)
        store.save(st)
    store.event(sid, "order_request", {"text": body.text, "type": res["type"]})
    return res


@app.post("/session/{sid}/order/confirm")
def order_confirm(sid: str):
    with _locks[sid]:
        st = _order_state(sid)
        try:
            order = flows.order_confirm(st)
        except ValueError as e:
            raise HTTPException(400, str(e))
        store.save(st)
    return order


@app.get("/session/{sid}/order/calendar.ics")
def order_calendar(sid: str):
    st = _order_state(sid)
    return Response(flows.order_ics(st), media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="{st["order"]["order_id"]}.ics"'})


@app.delete("/user/{user_id}")
def forget_user(user_id: str):
    store.save_user(user_id, {})
    return {"ok": True}


@app.get("/metrics")
def metrics():
    return summary(store.all_states())


@app.get("/graph")
def graph():
    return flows.graphs_mermaid()


_front = Path(__file__).resolve().parent.parent.parent / "frontend"
_videos = _front / "public" / "videos"
if _videos.exists():  # ролики отдаём прямо из public — без пересборки фронта
    app.mount("/videos", StaticFiles(directory=_videos), name="videos")
_dist = _front / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="front")
