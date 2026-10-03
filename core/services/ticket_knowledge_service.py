"""Поиск по базе знаний в контексте заявок."""

from sqlalchemy.ext.asyncio import AsyncSession

from core.models import KnowledgeBase
from core.repositories.ticket_repo import TicketRepository
from core.services.ticket_routing_service import _resolve_department, keyword_matches


async def find_knowledge_entry(
    session: AsyncSession,
    description: str,
    department_name: str | None = None,
) -> KnowledgeBase | None:
    """Вернуть первую запись БЗ, чьи ключевые слова совпадают с описанием."""
    department_id = None
    if department_name:
        department = await _resolve_department(session, department_name)
        if department is None:
            return None
        department_id = department.id

    entries = await TicketRepository(session).knowledge_entries(department_id)
    return next(
        (entry for entry in entries if keyword_matches(entry.keywords or "", description)),
        None,
    )
