"""Бизнес-правила маршрутизации заявок по отделам и ключевым словам."""

from sqlalchemy.ext.asyncio import AsyncSession

from core.database import async_session_maker
from core.models import Department, KnowledgeBase
from core.repositories.ticket_repo import TicketRepository

_CANONICAL_KEYWORDS: tuple[tuple[str, ...], ...] = (
    ("культ",),
    ("жил", "жыл", "быт"),
    ("корп",),
    ("информ",),
)


def keyword_matches(keywords: str, text: str) -> bool:
    """Проверить, встречается ли любое ключевое слово в тексте."""
    normalized = text.casefold()
    return any(
        word.strip().casefold() in normalized for word in keywords.split(",") if word.strip()
    )


def _match_department_by_keywords(name: str, candidates: list[Department]) -> Department | None:
    """Сопоставить название отдела по ключевым корням или частичному вхождению."""
    raw_lower = name.lower()
    for roots in _CANONICAL_KEYWORDS:
        if any(root in raw_lower for root in roots):
            for department in candidates:
                department_name = department.name.lower()
                if any(root in department_name for root in roots):
                    return department
    for department in candidates:
        department_name = department.name.lower()
        if department_name in raw_lower or raw_lower in department_name:
            return department
    return None


async def _resolve_department(
    session: AsyncSession,
    department_name: str | None,
) -> Department | None:
    """Найти отдел по названию или синонимам."""
    if not department_name or not department_name.strip():
        return None
    repository = TicketRepository(session)
    raw_name = department_name.strip()
    department = await repository.department_by_name(raw_name)
    if department is not None:
        return department
    departments = await repository.list_departments()
    return _match_department_by_keywords(raw_name, departments) if departments else None


def _match_department_in_memory(
    raw: str,
    all_depts: list[Department],
    kb_entries: list[KnowledgeBase] | None = None,
) -> Department | None:
    stripped = raw.strip().casefold()
    for department in all_depts:
        if department.name.casefold() == stripped:
            return department
    matched = _match_department_by_keywords(raw, all_depts)
    if matched is not None:
        return matched
    if kb_entries:
        dept_by_id = {department.id: department for department in all_depts}
        for entry in kb_entries:
            if entry.keywords and keyword_matches(entry.keywords, raw):
                found_department = dept_by_id.get(entry.department_id)
                if found_department is not None:
                    return found_department
    return None


async def sync_unassigned_ticket_departments() -> int:
    """Привязать нераспределённые заявки к отделам по теме/описанию."""
    updated_count = 0
    async with async_session_maker() as session:
        repository = TicketRepository(session)
        departments = await repository.list_departments()
        if not departments:
            return 0
        knowledge_entries = await repository.list_routing_knowledge()
        unassigned_tickets = await repository.list_unassigned_tickets()

        for ticket in unassigned_tickets:
            target_department = None
            if ticket.topic:
                target_department = _match_department_in_memory(
                    ticket.topic, departments, knowledge_entries
                )
            if target_department is None and ticket.description:
                target_department = _match_department_in_memory(
                    ticket.description, departments, knowledge_entries
                )
            if target_department is not None:
                ticket.department_id = target_department.id
                updated_count += 1

        if updated_count > 0:
            await session.commit()
    return updated_count
