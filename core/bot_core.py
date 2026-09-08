from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from core.config import settings
from core.database import async_session_maker
from core.models import Admin, Log, Ticket, TicketStatus, User


class BotCore:
    @staticmethod
    async def get_or_create_user(vk_id: int) -> User:
        async with async_session_maker() as session:
            db_user = await session.scalar(
                select(User).where(User.vk_id == vk_id)
            )
            if db_user is not None:
                return db_user
            db_user = User(vk_id=vk_id)
            session.add(db_user)
            try:
                await session.commit()
                await session.refresh(db_user)
            except IntegrityError:
                # Race condition: пользователь создан другим потоком
                await session.rollback()
                db_user = await session.scalar(
                    select(User).where(User.vk_id == vk_id)
                )
                if db_user is None:
                    raise RuntimeError(
                        f"Не удалось получить пользователя vk_id={vk_id} после race condition"
                    ) from None
                return db_user
            return db_user

    @staticmethod
    def is_admin_vk_id(vk_id: int | None) -> bool:
        return vk_id in settings.ADMIN_VK_IDS

    @staticmethod
    async def is_admin(user: User) -> bool:
        if user.vk_id is None:
            return False
        if BotCore.is_admin_vk_id(user.vk_id):
            return True
        async with async_session_maker() as session:
            admin = await session.scalar(
                select(Admin)
                .join(User, Admin.user_id == User.id)
                .where(User.vk_id == user.vk_id)
            )
            return admin is not None

    @staticmethod
    async def get_users() -> list[User]:
        async with async_session_maker() as session:
            result = await session.scalars(select(User).order_by(User.id))
            return list(result)

    @staticmethod
    async def get_user_tickets_count(user: User) -> int:
        if user.vk_id is None:
            return 0
        async with async_session_maker() as session:
            count = await session.scalar(
                select(func.count(Ticket.id))
                .join(User, Ticket.user_id == User.id)
                .where(User.vk_id == user.vk_id)
                .where(Ticket.status.not_in((TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO)))
            )
            return int(count or 0)

    @staticmethod
    async def log_action(user: User, action: str, details: str = "") -> None:
        """Записывает действие пользователя в журнал."""
        async with async_session_maker() as session:
            log_entry = Log(
                user_id=user.id,
                action=action,
                details=details,
            )
            session.add(log_entry)
            await session.commit()
