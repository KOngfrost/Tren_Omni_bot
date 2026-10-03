"""SSE (Server-Sent Events) маршрут для обновления в реальном времени.

Обеспечивает потоковую доставку счётчиков уведомлений и статистики дашборда
авторизованным администраторам без перезагрузки страницы.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from core.database import async_session_maker
from core.models import PartnershipRequest, Ticket, TicketStatus
from core.time_utils import day_start_app_tz, format_app_datetime
from web.dependencies import get_admin_scope

logger = logging.getLogger(__name__)

router = APIRouter()

sse_wakeup_event = asyncio.Event()
_subscribers: set[asyncio.Event] = {sse_wakeup_event}


def trigger_sse_update() -> None:
    """Оповестить все активные SSE-соединения о наличии новых данных."""
    for event in _subscribers:
        with contextlib.suppress(Exception):
            event.set()


async def get_dashboard_stats(session, user, is_super, dept_id):
    scope = [] if is_super else [Ticket.department_id == dept_id]

    rows = await session.execute(
        select(Ticket.status, func.count(Ticket.id)).where(*scope).group_by(Ticket.status)
    )
    ticket_counts = {status: count for status, count in rows.all() if status is not None}

    unassigned_rows = await session.execute(
        select(Ticket.status, func.count(Ticket.id))
        .where(Ticket.department_id.is_(None))
        .group_by(Ticket.status)
    )
    unassigned_by_status = {
        status: count for status, count in unassigned_rows.all() if status is not None
    }
    unassigned_total = sum(unassigned_by_status.values())
    unassigned_new = unassigned_by_status.get(TicketStatus.NEW, 0)

    completed_today = int(
        (
            await session.scalar(
                select(func.count(Ticket.id))
                .where(*scope)
                .where(Ticket.status.in_([TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO]))
                .where(Ticket.updated_at >= day_start_app_tz())
            )
        )
        or 0
    )

    total_tickets = sum(ticket_counts.values())
    new_tickets = ticket_counts.get(TicketStatus.NEW, 0)
    in_progress = ticket_counts.get(TicketStatus.IN_PROGRESS, 0)

    return {
        "total_tickets": total_tickets,
        "new_tickets": new_tickets,
        "in_progress": in_progress,
        "completed_today": completed_today,
        "unassigned_total": unassigned_total,
        "unassigned_new": unassigned_new,
    }


def _build_ticket_item(t: Ticket) -> dict:
    """Сформировать элемент уведомления из заявки."""
    is_reply = bool(t.response_text and t.response_text.strip())
    is_general = t.department_id is None
    dept_name = t.department.name if t.department else "Без отдела"
    preview = (t.description or "").strip()
    if len(preview) > 90:
        preview = preview[:90] + "..."
    created_str = f"{format_app_datetime(t.created_at, '%d.%m %H:%M')} МСК" if t.created_at else ""
    general_label = "Общее обращение" if is_general else ""

    if is_reply:
        return {
            "id": t.id,
            "type": "student_reply",
            "type_label": "Ответ студента",
            "title": f"Ответ по заявке #{t.id}",
            "icon": "message-square",
            "badge_class": "badge-reply",
            "department": dept_name,
            "is_general": is_general,
            "general_label": general_label,
            "text": preview or "Студент направил дополнение к заявке",
            "time": created_str,
            "url": f"/tickets/?open={t.id}",
        }
    return {
        "id": t.id,
        "type": "new_ticket",
        "type_label": "Новая заявка",
        "title": f"Заявка #{t.id}",
        "icon": "ticket",
        "badge_class": "badge-ticket",
        "department": dept_name,
        "is_general": is_general,
        "general_label": general_label,
        "text": preview or (t.topic or "Новое обращение"),
        "time": created_str,
        "url": f"/tickets/?open={t.id}",
    }


_STATUS_BADGE_MAP = {
    TicketStatus.NEW: ("badge-new", "Новая"),
    TicketStatus.IN_PROGRESS: ("badge-progress", "В работе"),
    TicketStatus.COMPLETED: ("badge-completed", "Решена"),
    TicketStatus.COMPLETED_AUTO: ("badge-completed", "Решена"),
}


def _build_recent_ticket(t: Ticket) -> dict:
    """Сформировать JSON-представление недавней заявки для SSE."""
    dept_name = t.department.name if t.department else "Без отдела"
    badge_class, status_label = _STATUS_BADGE_MAP.get(t.status, ("", str(t.status)))
    return {
        "id": t.id,
        "topic": t.topic or "Без темы",
        "department": dept_name,
        "status": t.status.value if hasattr(t.status, "value") else str(t.status),
        "status_badge": badge_class,
        "status_label": status_label,
        "created_at": format_app_datetime(t.created_at, "%d.%m.%Y %H:%M") if t.created_at else "",
    }


async def fetch_counters_and_dashboard(user: dict) -> dict:
    """Собрать данные счётчиков, уведомлений и статистики дашборда."""
    new_tickets_count = 0
    student_replies_count = 0
    new_partnerships_count = 0
    items: list[dict] = []

    async with async_session_maker() as session:
        is_super, dept_id = await get_admin_scope(session, user)

        base_scope = [Ticket.status == TicketStatus.NEW]
        if not is_super and dept_id:
            base_scope.append(or_(Ticket.department_id == dept_id, Ticket.department_id.is_(None)))

        new_scope = [
            *base_scope,
            or_(Ticket.response_text.is_(None), Ticket.response_text == ""),
        ]
        reply_scope = [
            *base_scope,
            and_(Ticket.response_text.is_not(None), Ticket.response_text != ""),
        ]

        new_tickets_count = (
            await session.scalar(select(func.count(Ticket.id)).where(*new_scope))
        ) or 0

        student_replies_count = (
            await session.scalar(select(func.count(Ticket.id)).where(*reply_scope))
        ) or 0

        if is_super:
            new_partnerships_count = (
                await session.scalar(
                    select(func.count(PartnershipRequest.id)).where(
                        PartnershipRequest.status == "new"
                    )
                )
            ) or 0

        stmt = (
            select(Ticket)
            .options(selectinload(Ticket.department), selectinload(Ticket.user))
            .where(*base_scope)
            .order_by(Ticket.updated_at.desc(), Ticket.created_at.desc())
            .limit(10)
        )
        tickets = list((await session.scalars(stmt)).all())
        items = [_build_ticket_item(t) for t in tickets]

        if is_super:
            pstmt = (
                select(PartnershipRequest)
                .where(PartnershipRequest.status == "new")
                .order_by(PartnershipRequest.created_at.desc())
                .limit(5)
            )
            partnerships = list((await session.scalars(pstmt)).all())
            for p in partnerships:
                p_text = (p.proposal_text or "").strip()
                if len(p_text) > 90:
                    p_text = p_text[:90] + "..."
                p_time = f"{p.created_at.strftime('%d.%m %H:%M')} МСК" if p.created_at else ""
                partner_title = p.user_name or p.contact_info or f"Заявка #{p.id}"
                items.append(
                    {
                        "id": p.id,
                        "type": "partnership",
                        "type_label": "Партнёрство",
                        "title": partner_title,
                        "icon": "handshake",
                        "badge_class": "badge-partner",
                        "department": "Заявка на сотрудничество",
                        "text": p_text or "Новое партнёрское предложение",
                        "time": p_time,
                        "url": "/partnerships/",
                    }
                )

        total = new_tickets_count + student_replies_count + new_partnerships_count

        dashboard_stats = await get_dashboard_stats(session, user, is_super, dept_id)

        recent_stmt = (
            select(Ticket)
            .options(selectinload(Ticket.department))
            .order_by(Ticket.created_at.desc())
            .limit(10)
        )
        if not is_super:
            if dept_id is not None:
                recent_stmt = recent_stmt.where(
                    or_(Ticket.department_id == dept_id, Ticket.department_id.is_(None))
                )
            else:
                recent_stmt = recent_stmt.where(Ticket.department_id.is_(None))
        recent_tickets_objs = list((await session.execute(recent_stmt)).scalars().all())
        recent_tickets = [_build_recent_ticket(t) for t in recent_tickets_objs]

    first_new_ticket = next((i for i in items if i["type"] == "new_ticket"), None)
    first_reply = next((i for i in items if i["type"] == "student_reply"), None)

    return {
        "new_tickets": new_tickets_count,
        "new_tickets_count": new_tickets_count,
        "student_replies": student_replies_count,
        "student_replies_count": student_replies_count,
        "new_partnerships": new_partnerships_count,
        "new_partnerships_count": new_partnerships_count,
        "total_notifications": total,
        "new_ticket_direct_url": first_new_ticket["url"]
        if (new_tickets_count == 1 and first_new_ticket)
        else "/tickets/?status=new",
        "student_reply_direct_url": first_reply["url"]
        if (student_replies_count == 1 and first_reply)
        else "/tickets/?status=new",
        "partnership_direct_url": "/partnerships/",
        "dashboard": dashboard_stats,
        "recent_tickets": recent_tickets,
        "items": items,
    }


_redis_listener_task: asyncio.Task[None] | None = None


async def _redis_pubsub_listener() -> None:
    """Фоновый подписчик на Redis Pub/Sub для мгновенного пробуждения всех SSE соединений."""
    from core.events import REDIS_TICKET_EVENTS_CHANNEL

    while True:
        try:
            from core.redis_client import get_redis_client

            redis = await get_redis_client()
            if redis is None:
                await asyncio.sleep(2.0)
                continue

            pubsub = redis.pubsub()
            await pubsub.subscribe(REDIS_TICKET_EVENTS_CHANNEL)
            logger.info("SSE воркер подписан на канал %s", REDIS_TICKET_EVENTS_CHANNEL)
            async for message in pubsub.listen():
                if message and message.get("type") == "message":
                    trigger_sse_update()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("Ошибка в подписчике Redis Pub/Sub для SSE: %s", exc)
            await asyncio.sleep(2.0)


def start_sse_redis_listener() -> asyncio.Task[None]:
    """Запустить фоновую задачу прослушивания Redis Pub/Sub для SSE."""
    global _redis_listener_task
    if _redis_listener_task is None or _redis_listener_task.done():
        _redis_listener_task = asyncio.create_task(_redis_pubsub_listener())
    return _redis_listener_task


async def sse_generator(request: Request, user: dict) -> AsyncGenerator[str, None]:
    event = asyncio.Event()
    _subscribers.add(event)
    last_ping = asyncio.get_event_loop().time()
    try:
        while True:
            if await request.is_disconnected():
                break

            try:
                data = await fetch_counters_and_dashboard(user)
                payload = json.dumps(data, ensure_ascii=False)
                yield f"event: counters\ndata: {payload}\n\n"
                yield f"data: {payload}\n\n"
            except Exception:
                logger.error("Error fetching SSE data", exc_info=True)

            try:
                # Мгновенное пробуждение по событию Redis / trigger_sse_update, либо таймаут 3 секунды
                await asyncio.wait_for(event.wait(), timeout=3.0)
                event.clear()
            except TimeoutError:
                pass

            now = asyncio.get_event_loop().time()
            if now - last_ping >= 30:
                yield "event: ping\ndata: {}\n\n"
                last_ping = now

    except asyncio.CancelledError:
        pass
    except Exception:
        logger.error("SSE connection error", exc_info=True)
    finally:
        _subscribers.discard(event)


@router.get("/stream")
async def sse_stream(request: Request):
    user = request.session.get("user")
    if not user:

        async def unauthorized_gen():
            yield f"event: error\ndata: {json.dumps({'detail': 'Unauthorized'})}\n\n"

        return StreamingResponse(
            unauthorized_gen(), status_code=401, media_type="text/event-stream"
        )

    return StreamingResponse(
        sse_generator(request, user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _process_ws_incoming_text(websocket: WebSocket, text: str) -> None:
    """Обработать входящее сообщение от WebSocket-клиента."""
    try:
        msg_obj = json.loads(text)
        if msg_obj.get("type") == "ping":
            await websocket.send_json({"type": "pong"})
    except Exception:
        pass


@router.websocket("/ws")
async def websocket_stream(websocket: WebSocket):
    """Двусторонний real-time WebSocket поток обновлений заявок и счётчиков."""
    session = websocket.scope.get("session", {})
    user = session.get("user")
    if not user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    event = asyncio.Event()
    _subscribers.add(event)
    try:
        data = await fetch_counters_and_dashboard(user)
        await websocket.send_json({"type": "counters", "data": data})

        while True:
            receive_task = asyncio.create_task(websocket.receive_text())
            wait_event_task = asyncio.create_task(event.wait())

            done, pending = await asyncio.wait(
                [receive_task, wait_event_task],
                timeout=25.0,
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()

            if receive_task in done:
                try:
                    msg_text = receive_task.result()
                    if msg_text:
                        await _process_ws_incoming_text(websocket, msg_text)
                except WebSocketDisconnect:
                    break
                except Exception:
                    pass

            if wait_event_task in done:
                event.clear()
                fresh_data = await fetch_counters_and_dashboard(user)
                await websocket.send_json({"type": "item_updated", "data": fresh_data})
                await websocket.send_json({"type": "counters", "data": fresh_data})
            elif not done:
                await websocket.send_json({"type": "ping"})

    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        logger.debug("WebSocket connection closed/error: %s", exc)
    finally:
        _subscribers.discard(event)
