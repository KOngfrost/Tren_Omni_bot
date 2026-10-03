"""SQL-запросы для чтения заявок и истории переписки."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.models import (
    Admin,
    Department,
    KnowledgeBase,
    Ticket,
    TicketMessage,
    TicketStatus,
    User,
    UserRole,
)


class TicketRepository:
    """Инкапсулирует read-запросы ticket domain для переданной сессии."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_user(
        self,
        vk_id: int,
        *,
        include_completed: bool,
        limit: int,
        offset: int,
        completed_statuses: set[TicketStatus],
    ) -> list[Ticket]:
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
            stmt = stmt.where(Ticket.status.not_in(completed_statuses))
        return list(await self.session.scalars(stmt))

    async def get_for_user(self, vk_id: int, ticket_id: int) -> Ticket | None:
        stmt = (
            select(Ticket)
            .join(User, Ticket.user_id == User.id)
            .options(selectinload(Ticket.department), selectinload(Ticket.user))
            .where(Ticket.id == ticket_id, User.vk_id == vk_id)
        )
        return await self.session.scalar(stmt)

    async def local_number(self, user_id: int, ticket_id: int) -> int:
        count = await self.session.scalar(
            select(func.count(Ticket.id)).where(
                Ticket.user_id == user_id,
                Ticket.id <= ticket_id,
            )
        )
        return count if count and count > 0 else 1

    async def mapping_for_user(self, vk_id: int) -> dict[int, int]:
        user = await self.session.scalar(select(User).where(User.vk_id == vk_id))
        if not user:
            return {}
        ticket_ids = list(
            (
                await self.session.scalars(
                    select(Ticket.id).where(Ticket.user_id == user.id).order_by(Ticket.id.asc())
                )
            ).all()
        )
        return {ticket_id: index + 1 for index, ticket_id in enumerate(ticket_ids)}

    async def get_by_local_number(self, vk_id: int, local_number: int) -> Ticket | None:
        user = await self.session.scalar(select(User).where(User.vk_id == vk_id))
        if not user:
            return None
        stmt = (
            select(Ticket)
            .options(selectinload(Ticket.department), selectinload(Ticket.user))
            .where(Ticket.user_id == user.id)
            .order_by(Ticket.id.asc())
            .offset(local_number - 1)
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def list_messages(self, ticket_id: int) -> list[TicketMessage]:
        result = await self.session.scalars(
            select(TicketMessage)
            .where(TicketMessage.ticket_id == ticket_id)
            .order_by(TicketMessage.created_at)
        )
        return list(result)

    async def lock_ticket(self, ticket_id: int) -> Ticket | None:
        stmt = (
            select(Ticket)
            .options(selectinload(Ticket.user), selectinload(Ticket.department))
            .where(Ticket.id == ticket_id)
            .with_for_update()
        )
        return await self.session.scalar(stmt)

    async def get_department(self, department_id: int) -> Department | None:
        return await self.session.get(Department, department_id)

    async def department_by_name(self, name: str) -> Department | None:
        return await self.session.scalar(select(Department).where(Department.name.ilike(name)))

    async def list_departments(self) -> list[Department]:
        return list((await self.session.scalars(select(Department))).all())

    async def list_routing_knowledge(self) -> list[KnowledgeBase]:
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.department_id.is_not(None),
            KnowledgeBase.keywords.is_not(None),
        )
        return list((await self.session.scalars(stmt)).all())

    async def list_unassigned_tickets(self) -> list[Ticket]:
        return list(
            (
                await self.session.scalars(select(Ticket).where(Ticket.department_id.is_(None)))
            ).all()
        )

    async def user_by_vk_id(self, vk_id: int) -> User | None:
        return await self.session.scalar(select(User).where(User.vk_id == vk_id))

    async def lock_user_ticket(self, ticket_id: int, vk_id: int) -> Ticket | None:
        stmt = (
            select(Ticket)
            .join(User, Ticket.user_id == User.id)
            .options(selectinload(Ticket.user), selectinload(Ticket.department))
            .where(Ticket.id == ticket_id, User.vk_id == vk_id)
            .with_for_update(of=Ticket)
        )
        return await self.session.scalar(stmt)

    async def admins_for_department(self, department_id: int) -> list[Admin]:
        stmt = (
            select(Admin)
            .options(selectinload(Admin.user))
            .where(Admin.department_id == department_id)
        )
        return list((await self.session.scalars(stmt)).all())

    async def superadmins(self) -> list[Admin]:
        stmt = (
            select(Admin)
            .options(selectinload(Admin.user))
            .where(Admin.role == UserRole.SUPERADMIN)
        )
        return list((await self.session.scalars(stmt)).all())

    async def assigned_admins(self) -> list[Admin]:
        stmt = select(Admin).options(selectinload(Admin.user)).where(Admin.user_id.is_not(None))
        return list((await self.session.scalars(stmt)).all())

    async def admins_for_ticket(self, department_id: int | None) -> list[Admin]:
        if department_id:
            return await self.admins_for_department(department_id)
        return await self.assigned_admins()

    async def knowledge_entries(self, department_id: int | None = None) -> list[KnowledgeBase]:
        stmt = (
            select(KnowledgeBase)
            .options(selectinload(KnowledgeBase.department))
            .where(KnowledgeBase.keywords.is_not(None))
            .order_by(KnowledgeBase.id)
        )
        if department_id is not None:
            stmt = stmt.where(KnowledgeBase.department_id == department_id)
        return list((await self.session.scalars(stmt)).all())

    def add(self, instance: object) -> None:
        self.session.add(instance)

    async def flush(self) -> None:
        await self.session.flush()
