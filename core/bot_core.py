from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import async_session_maker
from core.models import Admin, Log, Ticket, TicketStatus, User, UserRole, WebRole, WebUser


async def get_admin_scope_for_vk_id(session: AsyncSession, vk_id: int) -> tuple[bool, int | None]:
    """Определить область видимости пользователя по VK ID (для бота)."""
    # Проверка таблицы назначенных администраторов
    admin = await session.scalar(
        select(Admin).join(User, Admin.user_id == User.id).where(User.vk_id == vk_id)
    )
    if admin is not None:
        is_super = admin.role == UserRole.SUPERADMIN
        return is_super, admin.department_id if not is_super else None

    # Проверка связанного пользователя веб-панели
    web_user = await session.scalar(
        select(WebUser)
        .join(Admin, WebUser.admin_id == Admin.id)
        .join(User, Admin.user_id == User.id)
        .where(User.vk_id == vk_id)
    )
    if web_user is not None and web_user.is_active:
        if web_user.role == WebRole.SUPERADMIN:
            return True, None
        if web_user.role == WebRole.DEPARTMENT_ADMIN and web_user.department_id:
            return False, web_user.department_id

    return False, None


class BotCore:
    @staticmethod
    async def get_or_create_user(vk_id: int) -> User:
        async with async_session_maker() as session:
            db_user = await session.scalar(select(User).where(User.vk_id == vk_id))
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
                db_user = await session.scalar(select(User).where(User.vk_id == vk_id))
                if db_user is None:
                    raise RuntimeError(
                        f"Не удалось получить пользователя vk_id={vk_id} после race condition"
                    ) from None
                # Внимание: сессия закрывается до возврата db_user. Если expire_on_commit=False уже стоит в sessionmaker, 
                # это ОК для скалярных атрибутов, но lazy-связи будут недоступны вне сессии.
                return db_user
            # Внимание: сессия закрывается до возврата db_user. Если expire_on_commit=False уже стоит в sessionmaker, 
            # это ОК для скалярных атрибутов, но lazy-связи будут недоступны вне сессии.
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
            # Проверка наличия записи администратора
            admin = await session.scalar(
                select(Admin).join(User, Admin.user_id == User.id).where(User.vk_id == user.vk_id)
            )
            if admin is not None:
                return True

            # Проверка учётной записи веб-панели через связанного администратора
            web_user = await session.scalar(
                select(WebUser)
                .join(Admin, WebUser.admin_id == Admin.id)
                .join(User, Admin.user_id == User.id)
                .where(User.vk_id == user.vk_id)
            )
            if web_user is not None and web_user.is_active:
                return web_user.role in (WebRole.SUPERADMIN, WebRole.DEPARTMENT_ADMIN)

            return False

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
