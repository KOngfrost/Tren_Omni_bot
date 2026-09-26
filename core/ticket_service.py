"""Сервис заявок: единая бизнес-логика для VK-бота и веб-панели.

Используется:
- VK-ботом: просмотр «Мои заявки», история переписки.
- Веб-панелью: ответ администратора, смена статуса, назначение отдела.

Все функции принимают/создают AsyncSession и работают через SQLAlchemy-модели.
"""

import logging
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.database import async_session_maker
from core.models import (
    Admin,
    Department,
    KnowledgeBase,
    Log,
    MessageAuthorType,
    Ticket,
    TicketMessage,
    TicketStatus,
    User,
)
from core.outbox import add_outbox_message, fire_outbox_delivery

logger = logging.getLogger(__name__)

# === Общий контекстный менеджер для транзакций ===


@asynccontextmanager
async def ticket_transaction():
    """Контекстный менеджер для работы с транзакциями заявок.

    Позволяет объединять несколько операций в одну транзакцию:

        async with ticket_transaction() as session:
            ticket = await session.get(Ticket, 123)
            await session.execute(...)  # другие операции
            # commit() вызывается автоматически при выходе из контекста

    Также можно вызывать несколько раз вложенно — сессия будет общая.
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# === Остальной код ===

# Допустимые переходы статусов заявки:
# NEW -> IN_PROGRESS -> TRANSFERRED_ADMIN/HOUSEKEEPING -> COMPLETED -> COMPLETED_AUTO
ALLOWED_TRANSITIONS: dict[TicketStatus, set[TicketStatus]] = {
    TicketStatus.NEW: {
        TicketStatus.IN_PROGRESS,
        TicketStatus.TRANSFERRED_ADMIN,
        TicketStatus.TRANSFERRED_HOUSEKEEPING,
        TicketStatus.COMPLETED,
    },
    TicketStatus.IN_PROGRESS: {
        TicketStatus.TRANSFERRED_ADMIN,
        TicketStatus.TRANSFERRED_HOUSEKEEPING,
        TicketStatus.COMPLETED,
    },
    TicketStatus.TRANSFERRED_ADMIN: {
        TicketStatus.IN_PROGRESS,
        TicketStatus.COMPLETED,
    },
    TicketStatus.TRANSFERRED_HOUSEKEEPING: {
        TicketStatus.IN_PROGRESS,
        TicketStatus.COMPLETED,
    },
    TicketStatus.COMPLETED: {TicketStatus.IN_PROGRESS, TicketStatus.COMPLETED_AUTO},
    TicketStatus.COMPLETED_AUTO: {TicketStatus.IN_PROGRESS},
    TicketStatus.ANONYMOUS: {
        TicketStatus.IN_PROGRESS,
        TicketStatus.TRANSFERRED_ADMIN,
        TicketStatus.TRANSFERRED_HOUSEKEEPING,
        TicketStatus.COMPLETED,
    },
}

COMPLETED_STATUSES = {TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO}

def status_label(status: "TicketStatus | str | None") -> str:
    """Русская метка статуса (безопасно для любого входа).

    Поскольку TicketStatus является StrEnum, его .value уже содержит
    человекочитаемое русское название (напр., «В обработке»).
    Отдельный словарь STATUS_LABELS был удалён как дублирующий .value и
    создающий риск рассинхронизации при добавлении новых статусов.
    """
    if status is None:
        return "—"
    if isinstance(status, TicketStatus):
        return status.value
    return status


class StatusTransitionError(ValueError):
    """Недопустимый переход статуса заявки."""


def can_transition(current: TicketStatus, new: TicketStatus) -> bool:
    """Проверить, допустим ли переход статуса."""
    return new in ALLOWED_TRANSITIONS.get(current, set())


def validate_transition(current: TicketStatus, new: TicketStatus) -> None:
    """Выбросить StatusTransitionError, если переход недопустим."""
    if current == new:
        return
    if not can_transition(current, new):
        raise StatusTransitionError(
            f"Недопустимый переход статуса: {current.value} -> {new.value}"
        )


async def get_user_tickets(
    vk_id: int,
    include_completed: bool = False,
    limit: int = 10,
    offset: int = 0,
) -> list[Ticket]:
    """Получить заявки пользователя по его VK ID (новые сверху).

    limit/offset дают пагинацию в боте («Показать ещё») и веб-панели.
    """
    async with async_session_maker() as session:
        stmt = (
            select(Ticket)
            .join(User, Ticket.user_id == User.id)
            .options(selectinload(Ticket.department), selectinload(Ticket.user))
            .where(User.vk_id == vk_id)
            .order_by(Ticket.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if not include_completed:
            stmt = stmt.where(Ticket.status.not_in(COMPLETED_STATUSES))
        result = await session.scalars(stmt)
        return list(result)


async def get_user_ticket_by_id(vk_id: int, ticket_id: int) -> Ticket | None:
    """Получить заявку конкретного пользователя по глобальному ticket_id с проверкой владельца."""
    async with async_session_maker() as session:
        stmt = (
            select(Ticket)
            .join(User, Ticket.user_id == User.id)
            .options(selectinload(Ticket.department), selectinload(Ticket.user))
            .where(Ticket.id == ticket_id, User.vk_id == vk_id)
        )
        return await session.scalar(stmt)


async def get_user_ticket_local_number(
    user_id: int, ticket_id: int, session: AsyncSession | None = None
) -> int:
    """Получить персональный (локальный) порядковый номер заявки для пользователя (1, 2, 3...)."""
    async def _query(s: AsyncSession) -> int:
        stmt = (
            select(func.count(Ticket.id))
            .where(Ticket.user_id == user_id, Ticket.id <= ticket_id)
        )
        count = await s.scalar(stmt)
        return count if count and count > 0 else 1

    if session is not None:
        return await _query(session)
    async with async_session_maker() as s:
        return await _query(s)


async def get_user_tickets_mapping(vk_id: int) -> dict[int, int]:
    """Словарь соответствия {global_ticket_id: local_number} для пользователя."""
    async with async_session_maker() as session:
        user = await session.scalar(select(User).where(User.vk_id == vk_id))
        if not user:
            return {}
        stmt = (
            select(Ticket.id)
            .where(Ticket.user_id == user.id)
            .order_by(Ticket.id.asc())
        )
        ticket_ids = list((await session.scalars(stmt)).all())
        return {tid: idx + 1 for idx, tid in enumerate(ticket_ids)}


async def get_user_ticket_by_local_number(
    vk_id: int, local_num: int
) -> tuple[Ticket | None, int]:
    """Найти заявку пользователя по её локальному порядковому номеру (1-indexed)."""
    if local_num <= 0:
        return None, local_num
    async with async_session_maker() as session:
        user = await session.scalar(select(User).where(User.vk_id == vk_id))
        if not user:
            return None, local_num
        stmt = (
            select(Ticket)
            .options(selectinload(Ticket.department), selectinload(Ticket.user))
            .where(Ticket.user_id == user.id)
            .order_by(Ticket.id.asc())
            .offset(local_num - 1)
            .limit(1)
        )
        ticket = await session.scalar(stmt)
        return ticket, local_num


async def resolve_user_ticket(vk_id: int, num: int) -> tuple[Ticket | None, int]:
    """Разрешить номер заявки: сначала как локальный номер пользователя, затем фоллбэк на глобальный ID."""
    ticket, local_num = await get_user_ticket_by_local_number(vk_id, num)
    if ticket is not None:
        return ticket, local_num
    ticket = await get_user_ticket_by_id(vk_id, num)
    if ticket is not None and ticket.user_id is not None and ticket.id is not None:
        computed_local = await get_user_ticket_local_number(ticket.user_id, ticket.id)
        return ticket, computed_local
    return None, num


async def get_ticket_messages(ticket_id: int) -> list[TicketMessage]:
    """Получить историю переписки по заявке."""
    async with async_session_maker() as session:
        result = await session.scalars(
            select(TicketMessage)
            .where(TicketMessage.ticket_id == ticket_id)
            .order_by(TicketMessage.created_at)
        )
        return list(result)


def add_ticket_message(
    session: AsyncSession,
    ticket: Ticket,
    author_type: MessageAuthorType,
    message: str,
    author_vk_id: int | None = None,
) -> TicketMessage:
    """Добавить сообщение в историю заявки (без commit — вызывает вызывающий код)."""
    entry = TicketMessage(
        ticket_id=ticket.id,
        author_type=author_type,
        author_vk_id=author_vk_id,
        message=message,
    )
    session.add(entry)
    return entry


async def reply_to_ticket(
    ticket_id: int,
    admin_username: str,
    message: str,
    complete: bool = False,
) -> tuple[Ticket | None, bool]:
    """Ответ администратора на заявку из веб-панели.

    Шаги (outbox-паттерн, устраняет потерю уведомлений):
    1. Открыть транзакцию, заблокировать строку заявки (FOR UPDATE),
       исключая конкурентное редактирование другими админами.
    2. Сохранить сообщение в ticket_messages, обновить статус, записать Log
       и положить VK-уведомление в таблицу vk_outbox — всё в ОДНОЙ транзакции.
    3. commit() — только после этого сообщение доставляется фоновым воркером
       (с повтором попыток), поэтому «ответ в VK без записи в БД» невозможен.

    Возвращает (заявка, запланирована_ли_доставка_в_VK) или (None, False),
    если заявка не найдена.
    """
    async with ticket_transaction() as session:
        ticket = await session.scalar(
            select(Ticket)
            .options(selectinload(Ticket.user), selectinload(Ticket.department))
            .where(Ticket.id == ticket_id)
            .with_for_update()
        )
        if ticket is None:
            return None, False

        # 1. История
        add_ticket_message(session, ticket, MessageAuthorType.ADMIN, message)

        # 2. Итоговый ответ
        ticket.response_text = message

        # 3. Статус
        if complete and ticket.status not in COMPLETED_STATUSES:
            validate_transition(ticket.status, TicketStatus.COMPLETED)
            ticket.status = TicketStatus.COMPLETED
        elif ticket.status == TicketStatus.NEW:
            ticket.status = TicketStatus.IN_PROGRESS

        # 4. Журнал
        session.add(
            Log(
                user_id=ticket.user_id,
                action="web_reply",
                details=(
                    f"Администратор {admin_username} ответил на заявку #{ticket.id}"
                    f" (статус: {ticket.status.value})"
                ),
            )
        )

        # 5. Откладываем VK-уведомление в ту же транзакцию (outbox)
        scheduled = False
        if not ticket.is_anonymous and ticket.user and ticket.user.vk_id:
            local_num = await get_user_ticket_local_number(
                ticket.user_id, ticket.id, session=session
            )
            dept_name = ticket.department.name if ticket.department else "—"
            add_outbox_message(
                session,
                ticket.user.vk_id,
                (
                    f"Ответ на вашу заявку #{local_num} "
                    f"({dept_name}):\n\n{message}"
                ),
            )
            scheduled = True

    # commit() уже вызван внутри ticket_transaction
    if scheduled:
        fire_outbox_delivery()

    return ticket, scheduled


async def change_ticket_status(
    ticket_id: int,
    new_status: TicketStatus,
    admin_username: str,
) -> Ticket | None:
    """Сменить статус заявки с проверкой допустимых переходов. Пишет действие в logs.

    Строка заявки блокируется (SELECT...FOR UPDATE), чтобы два администратора
    не могли одновременно изменить статус одной заявки.
    """
    async with ticket_transaction() as session:
        ticket = await session.scalar(
            select(Ticket)
            .options(selectinload(Ticket.user), selectinload(Ticket.department))
            .where(Ticket.id == ticket_id)
            .with_for_update()
        )
        if ticket is None:
            return None

        validate_transition(ticket.status, new_status)
        old_status = ticket.status
        ticket.status = new_status

        add_ticket_message(
            session,
            ticket,
            MessageAuthorType.SYSTEM,
            f"Статус изменён: {old_status.value} -> {new_status.value} (администратор {admin_username})",
        )
        session.add(
            Log(
                user_id=ticket.user_id,
                action="web_status_change",
                details=(
                    f"Администратор {admin_username} изменил статус заявки "
                    f"#{ticket.id}: {old_status.value} -> {new_status.value}"
                ),
            )
        )
        scheduled = False
        if not ticket.is_anonymous and ticket.user and ticket.user.vk_id:
            local_num = await get_user_ticket_local_number(
                ticket.user_id, ticket.id, session=session
            )
            add_outbox_message(
                session,
                ticket.user.vk_id,
                f"Статус вашей заявки #{local_num} изменён: {status_label(ticket.status)}",
            )
            scheduled = True

    if scheduled:
        fire_outbox_delivery()
    return ticket


async def assign_ticket_department(
    ticket_id: int,
    department_id: int,
    admin_username: str,
) -> Ticket | None:
    """Переназначить заявку другому отделу (только суперадмин). Пишет действие в logs.

    Строка заявки блокируется (SELECT...FOR UPDATE) для защиты от
    конкурентной передачи в разные отделы.
    """
    async with ticket_transaction() as session:
        ticket = await session.scalar(
            select(Ticket)
            .options(selectinload(Ticket.department), selectinload(Ticket.user))
            .where(Ticket.id == ticket_id)
            .with_for_update()
        )
        department = await session.get(Department, department_id)
        if ticket is None or department is None:
            return None

        old_department = ticket.department.name if ticket.department else "—"
        ticket.department_id = department_id

        add_ticket_message(
            session,
            ticket,
            MessageAuthorType.SYSTEM,
            f"Заявка передана из отдела «{old_department}» в отдел «{department.name}» "
            f"(администратор {admin_username})",
        )
        session.add(
            Log(
                user_id=ticket.user_id,
                action="web_assign",
                details=(
                    f"Администратор {admin_username} передал заявку #{ticket.id} "
                    f"из «{old_department}» в «{department.name}»"
                ),
            )
        )
        return ticket


def format_ticket_list(
    tickets: list[Ticket], user_ticket_map: dict[int, int] | None = None
) -> str:
    """Список заявок для VK-сообщения: номер, отдел, тема, дата, статус, ответ.

    user_ticket_map: словарь {global_id: local_number} для персональной нумерации.
    Если None — используется глобальный ID заявки.
    """
    if not tickets:
        return "У вас пока нет заявок."

    lines = [f"Ваши заявки (последние {len(tickets)}):", ""]
    for ticket in tickets:
        created = ticket.created_at.strftime("%d.%m.%Y") if ticket.created_at else "—"
        dept = ticket.department.name if ticket.department else "—"
        # Используем локальный номер если есть, иначе глобальный ID
        display_number = ticket.id
        if ticket.id is not None and user_ticket_map:
            display_number = user_ticket_map.get(ticket.id, ticket.id)
        lines.append(f"#{display_number} · {dept} · {ticket.topic or 'Без темы'}")
        lines.append(f"   Статус: {status_label(ticket.status)} · создана {created}")
        if ticket.description:
            preview = " ".join(ticket.description.split())
            lines.append(f"   Вопрос: {preview[:160]}{'...' if len(preview) > 160 else ''}")
        if ticket.response_text:
            preview = ticket.response_text[:120]
            lines.append(f"   Ответ: {preview}{'…' if len(ticket.response_text) > 120 else ''}")
        lines.append("")
    lines.append("Нажмите «Подробнее #N», чтобы увидеть историю заявки.")
    return "\n".join(lines)


def format_ticket_details(
    ticket: Ticket,
    messages: list[TicketMessage],
    local_id: int | None = None,
) -> str:
    """Подробная карточка заявки с историей переписки."""
    created = ticket.created_at.strftime("%d.%m.%Y %H:%M") if ticket.created_at else "—"
    dept = ticket.department.name if ticket.department else "—"
    display_id = local_id if local_id is not None else ticket.id

    lines = [
        f"Заявка #{display_id}",
        f"Отдел: {dept}",
        f"Тема: {ticket.topic or 'Без темы'}",
        f"Статус: {status_label(ticket.status)}",
        f"Создана: {created}",
        "",
        "История обращений:",
        "— — —",
    ]

    if not messages:
        lines.append(f"Вы: {ticket.description or '—'}")
        if ticket.response_text:
            lines.append(f"Администратор: {ticket.response_text}")
    else:
        for msg in messages:
            if msg.author_type is None:
                continue
            author = {
                MessageAuthorType.USER: "Вы",
                MessageAuthorType.ADMIN: "Администратор",
                MessageAuthorType.SYSTEM: "Система",
            }.get(msg.author_type, "—")
            when = msg.created_at.strftime("%d.%m.%Y %H:%M") if msg.created_at else ""
            lines.append(f"[{when}] {author}: {msg.message}")

    return "\n".join(lines)


def mask_anonymous_data(full_name: str | None, is_anonymous: bool) -> str:
    """Маскировка персональных данных анонимных заявок."""
    if is_anonymous:
        return "Аноним"
    return full_name or "—"


# === Создание обращений ===


_CANONICAL_KEYWORDS: tuple[tuple[str, ...], ...] = (
    ("культ",),
    ("жил", "жыл", "быт"),
    ("корп",),
    ("информ",),
)


def _match_department_by_keywords(name: str, candidates: list[Department]) -> Department | None:
    """Сопоставить название отдела по ключевым корням или частичному вхождению."""
    raw_lower = name.lower()
    for roots in _CANONICAL_KEYWORDS:
        if any(r in raw_lower for r in roots):
            for dept in candidates:
                d_lower = dept.name.lower()
                if any(r in d_lower for r in roots):
                    return dept
    for dept in candidates:
        d_lower = dept.name.lower()
        if d_lower in raw_lower or raw_lower in d_lower:
            return dept
    return None


async def _resolve_department(
    session: AsyncSession,
    department_name: str | None,
) -> Department | None:
    """Найти отдел по названию или синонимам (полным/сокращённым)."""
    if not department_name or not department_name.strip():
        return None
    raw = department_name.strip()

    dept = await session.scalar(select(Department).where(Department.name.ilike(raw)))
    if dept is not None:
        return dept

    all_depts = list((await session.scalars(select(Department))).all())
    return _match_department_by_keywords(raw, all_depts) if all_depts else None


def _match_department_in_memory(
    raw: str,
    all_depts: list[Department],
    kb_entries: list[KnowledgeBase] | None = None,
) -> Department | None:
    stripped = raw.strip().casefold()
    for dept in all_depts:
        if dept.name.casefold() == stripped:
            return dept
    matched = _match_department_by_keywords(raw, all_depts)
    if matched is not None:
        return matched
    if kb_entries:
        dept_by_id = {d.id: d for d in all_depts}
        for kb in kb_entries:
            if kb.keywords and keyword_matches(kb.keywords, raw):
                found_dept = dept_by_id.get(kb.department_id)
                if found_dept is not None:
                    return found_dept
    return None


async def sync_unassigned_ticket_departments() -> int:
    """Привязать заявки с пустым department_id к соответствующим отделам по теме/описанию."""
    updated_count = 0
    async with async_session_maker() as session:
        all_depts = list((await session.scalars(select(Department))).all())
        if not all_depts:
            return 0

        kb_entries = list(
            (
                await session.scalars(
                    select(KnowledgeBase).where(
                        KnowledgeBase.department_id.is_not(None),
                        KnowledgeBase.keywords.is_not(None),
                    )
                )
            ).all()
        )

        unassigned = list(
            (await session.scalars(select(Ticket).where(Ticket.department_id.is_(None)))).all()
        )

        for ticket in unassigned:
            target_dept = None
            if ticket.topic:
                target_dept = _match_department_in_memory(ticket.topic, all_depts, kb_entries)
            if target_dept is None and ticket.description:
                target_dept = _match_department_in_memory(
                    ticket.description, all_depts, kb_entries
                )
            if target_dept is not None:
                ticket.department_id = target_dept.id
                updated_count += 1

        if updated_count > 0:
            await session.commit()
    return updated_count


async def create_ticket(
    topic: str,
    description: str,
    vk_id: int | None = None,
    keep_identity: bool = False,
    department_name: str | None = None,
) -> Ticket:
    """Создать обращение студента из раздела меню бота.

    keep_identity=True сохраняет пользователя (VK ID), чтобы администратор
    мог ответить через VK. Иначе обращение хранится без привязки к VK ID.

    department_name связывает заявку с отделом (по названию из departments);
    если отдел не найден, заявка создаётся без отдела.
    """
    async with ticket_transaction() as session:
        user = None
        if keep_identity and vk_id is not None:
            user = await session.scalar(select(User).where(User.vk_id == vk_id))
            if user is None:
                user = User(vk_id=vk_id)
                session.add(user)
                await session.flush()

        department = await _resolve_department(session, department_name)

        ticket = Ticket(
            user_id=user.id if user else None,
            department=department,
            topic=topic,
            description=description,
            status=TicketStatus.NEW if keep_identity else TicketStatus.ANONYMOUS,
            is_anonymous=not keep_identity,
            auto_closed=False,
        )
        session.add(ticket)
        await session.flush()  # получаем ticket.id

        # Добавляем сообщение в историю
        add_ticket_message(
            session,
            ticket,
            MessageAuthorType.USER,
            description,
            author_vk_id=vk_id if keep_identity else None,
        )

        # Журналируем создание
        session.add(
            Log(
                user_id=user.id if user else None,
                action="ticket_created",
                details=f"Создана заявка #{ticket.id}: {topic}",
            )
        )

        scheduled = False
        if department and department.id:
            admins = list(
                (
                    await session.scalars(
                        select(Admin)
                        .options(selectinload(Admin.user))
                        .where(Admin.department_id == department.id)
                    )
                ).all()
            )
            for admin in admins:
                if admin.user and admin.user.vk_id:
                    student_info = mask_anonymous_data(
                        user.full_name if user else None, ticket.is_anonymous
                    )
                    dorm_info = (
                        f" ({user.dormitory})"
                        if (user and user.dormitory and not ticket.is_anonymous)
                        else ""
                    )
                    add_outbox_message(
                        session,
                        admin.user.vk_id,
                        (
                            f"📩 Новая заявка #{ticket.id} [{department.name}]\n"
                            f"От: {student_info}{dorm_info}\n"
                            f"Тема: {topic}\n\n"
                            f"Текст: {description}\n\n"
                            f"👉 Для работы напишите: «Заявка #{ticket.id}»"
                        ),
                    )
                    scheduled = True

    if scheduled:
        fire_outbox_delivery()

    return ticket

async def add_student_reply(ticket_id: int, vk_id: int, message: str) -> Ticket | None:
    """Добавить ответ студента в принадлежащую ему заявку.

    Если заявка закрыта (COMPLETED / COMPLETED_AUTO), она возвращается
    в состояние IN_PROGRESS. Администраторам отдела отправляется
    уведомление через outbox.
    """
    async with ticket_transaction() as session:
        ticket = await session.scalar(
            select(Ticket)
            .join(User, Ticket.user_id == User.id)
            .options(selectinload(Ticket.user), selectinload(Ticket.department))
            .where(Ticket.id == ticket_id, User.vk_id == vk_id)
            .with_for_update(of=Ticket)
        )
        if ticket is None or ticket.is_anonymous:
            return None

        # Добавляем сообщение в историю
        add_ticket_message(session, ticket, MessageAuthorType.USER, message, author_vk_id=vk_id)

        # Ответ студента переводит заявку в статус NEW (Новое / Требует ответа),
        # чтобы администраторы видели её в очереди нерассмотренных и загорался огонёк/счётчик
        old_status_value = ticket.status.value
        ticket.status = TicketStatus.NEW
        add_ticket_message(
            session,
            ticket,
            MessageAuthorType.SYSTEM,
            f"Студент направил уточняющий вопрос/ответ: статус возвращён в «Новое» для рассмотрения администратором (был «{old_status_value}»)",
        )

        # Журнал
        session.add(
            Log(
                user_id=ticket.user_id,
                action="student_reply",
                details=f"Студент направил ответ в заявку #{ticket.id} (статус переведён в «Новое»)",
            )
        )

        scheduled = False
        # Уведомляем администраторов отдела через outbox
        if ticket.department and ticket.department_id:
            admins = await session.scalars(
                select(Admin)
                .options(selectinload(Admin.user))
                .where(Admin.department_id == ticket.department_id)
            )
            for admin in admins:
                if admin.user and admin.user.vk_id:
                    subject = "Повторно открыто" if reopened else "Новый ответ студента"
                    add_outbox_message(
                        session,
                        admin.user.vk_id,
                        (f"⚠️ {subject}\n\nСтудент ответил на заявку #{ticket.id}:\n\n{message}"),
                    )
                    scheduled = True
            # WebUser уведомления через VK не отправляются (нет vk_id)

    if scheduled:
        fire_outbox_delivery()
    return ticket


# === База знаний: единая логика поиска для бота и веб-панели ===


def keyword_matches(keywords: str, text: str) -> bool:
    """True, если хотя бы одно ключевое слово встречается в тексте.

    Ключевые слова хранятся строкой через запятую. Сравнение
    регистронезависимое (casefold); пустые слова игнорируются.
    """
    normalized = text.casefold()
    return any(
        word.strip().casefold() in normalized for word in keywords.split(",") if word.strip()
    )


async def find_knowledge_entry(
    session: AsyncSession,
    description: str,
    department_name: str | None = None,
) -> KnowledgeBase | None:
    """Первая запись БЗ, ключевые слова которой совпадают с описанием.

    Используется:
    - VK-ботом: подсказка перед созданием заявки (рутинные вопросы);
    - веб-маршрутами: когда панели понадобится поиск по БЗ.

    Если задан department_name — поиск ограничен записями отдела
    (записи без отдела в этом случае не рассматриваются).
    """
    stmt = (
        select(KnowledgeBase)
        .options(selectinload(KnowledgeBase.department))
        .where(KnowledgeBase.keywords.is_not(None))
        .order_by(KnowledgeBase.id)
    )
    if department_name:
        resolved_dept = await _resolve_department(session, department_name)
        if resolved_dept is not None:
            stmt = stmt.where(KnowledgeBase.department_id == resolved_dept.id)
        else:
            return None
    entries = list(await session.scalars(stmt))
    return next(
        (entry for entry in entries if keyword_matches(entry.keywords or "", description)),
        None,
    )
