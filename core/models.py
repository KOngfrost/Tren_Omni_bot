import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class UserRole(enum.StrEnum):
    ADMIN = "admin"
    SUPERADMIN = "superadmin"


class WebRole(enum.StrEnum):
    """Роли пользователей веб-панели управления."""

    SUPERADMIN = "SUPERADMIN"
    DEPARTMENT_ADMIN = "DEPARTMENT_ADMIN"


class MessageAuthorType(enum.StrEnum):
    """Автор сообщения в истории заявки."""

    USER = "user"  # студент (VK)
    ADMIN = "admin"  # администратор (веб-панель)
    SYSTEM = "system"  # системные события (смена статуса и т.п.)


class TicketStatus(enum.StrEnum):
    NEW = "Новое"
    IN_PROGRESS = "В обработке"
    TRANSFERRED_ADMIN = "Передано в администрацию СГ"
    TRANSFERRED_HOUSEKEEPING = "Передано в Хозчасть"
    COMPLETED = "Выполнено"
    COMPLETED_AUTO = "Выполнено (авто)"
    ANONYMOUS = "Анонимное"


class Department(Base):
    __tablename__ = "departments"

    id = mapped_column(Integer, primary_key=True)
    name = mapped_column(String, unique=True, nullable=False)

    admins: Mapped[list["Admin"]] = relationship("Admin", back_populates="department")
    tickets: Mapped[list["Ticket"]] = relationship("Ticket", back_populates="department")
    knowledge_base: Mapped[list["KnowledgeBase"]] = relationship(
        "KnowledgeBase", back_populates="department"
    )
    faq_nodes: Mapped[list["FAQNode"]] = relationship("FAQNode", back_populates="department")
    events: Mapped[list["Event"]] = relationship("Event", back_populates="department")


class User(Base):
    __tablename__ = "users"

    id = mapped_column(Integer, primary_key=True)
    vk_id = mapped_column(BigInteger, unique=True, nullable=False)
    full_name = mapped_column(String, nullable=True)
    dormitory = mapped_column(String, nullable=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())

    tickets: Mapped[list["Ticket"]] = relationship("Ticket", back_populates="user")
    subscriptions: Mapped[list["Subscription"]] = relationship(
        "Subscription", back_populates="user"
    )
    registrations: Mapped[list["Registration"]] = relationship(
        "Registration", back_populates="user"
    )
    logs: Mapped[list["Log"]] = relationship("Log", back_populates="user")


class Admin(Base):
    __tablename__ = "admins"

    id = mapped_column(Integer, primary_key=True)
    user_id = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    department_id = mapped_column(
        Integer, ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    role = mapped_column(SAEnum(UserRole), default=UserRole.ADMIN)

    user: Mapped["User"] = relationship("User")
    department: Mapped["Department | None"] = relationship("Department", back_populates="admins")
    web_user: Mapped["WebUser | None"] = relationship("WebUser", back_populates="admin")


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        Index("ix_tickets_user_id", "user_id"),
        Index("ix_tickets_department_id", "department_id"),
        Index("ix_tickets_status", "status"),
        Index("ix_tickets_created_at", "created_at"),
    )

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    department_id = mapped_column(
        Integer, ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    topic = mapped_column(String, nullable=True)
    description = mapped_column(Text, nullable=True)
    status = mapped_column(SAEnum(TicketStatus), default=TicketStatus.NEW, nullable=False)
    response_text = mapped_column(Text, nullable=True)
    is_anonymous = mapped_column(Boolean, default=False, nullable=False)
    auto_closed = mapped_column(Boolean, default=False, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User | None"] = relationship("User", back_populates="tickets")
    department: Mapped["Department | None"] = relationship("Department", back_populates="tickets")
    messages: Mapped[list["TicketMessage"]] = relationship(
        "TicketMessage",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketMessage.created_at",
    )


class TicketMessage(Base):
    """Сообщение в истории заявки (вопрос студента, ответ администратора, системные события)."""

    __tablename__ = "ticket_messages"
    __table_args__ = (
        Index("ix_ticket_messages_ticket_id", "ticket_id"),
        CheckConstraint(
            "author_type IN ('USER', 'ADMIN', 'SYSTEM')",
            name="ck_ticket_messages_author_type",
        ),
    )

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id = mapped_column(
        Integer,
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_type = mapped_column(SAEnum(MessageAuthorType), nullable=False)
    author_vk_id = mapped_column(BigInteger, nullable=True)  # VK ID автора, если это студент
    message = mapped_column(Text, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="messages")


class WebUser(Base):
    """Пользователь веб-админки (пароль хранится только в виде хеша)."""

    __tablename__ = "web_users"

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    username = mapped_column(String, unique=True, nullable=False)
    password_hash = mapped_column(
        String, nullable=False
    )  # формат: pbkdf2_sha256$iterations$salt$hash
    role = mapped_column(SAEnum(WebRole), default=WebRole.DEPARTMENT_ADMIN, nullable=False)
    admin_id = mapped_column(Integer, ForeignKey("admins.id", ondelete="CASCADE"), nullable=True)
    department_id = mapped_column(
        Integer, ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    is_active = mapped_column(Boolean, default=True, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at = mapped_column(DateTime(timezone=True), nullable=True)

    department: Mapped["Department | None"] = relationship("Department")
    admin: Mapped["Admin | None"] = relationship("Admin")


class ReportRun(Base):
    """Факт отправки ежедневного отчёта — защита от повторных отправок."""

    __tablename__ = "report_runs"

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date = mapped_column(
        Date, unique=True, nullable=False
    )  # дата отчёта (за которую он сформирован)
    status = mapped_column(String, default="sent", nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeBase(Base):
    """Модель базы знаний: ключевые слова → заготовленный ответ отдела."""

    __tablename__ = "knowledge_base"

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    department_id = mapped_column(
        Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    keywords = mapped_column(Text, nullable=False)
    answer = mapped_column(Text, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    department: Mapped["Department"] = relationship("Department", back_populates="knowledge_base")


class FAQNode(Base):
    """Модель FAQ-дерева: вопросы-кнопки и финальные узлы с ответом."""

    __tablename__ = "faq_nodes"
    __table_args__ = (
        # Для финальных узлов обязателен final_answer
        CheckConstraint(
            "NOT is_final OR final_answer IS NOT NULL",
            name="ck_faq_final_answer_required",
        ),
    )

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    department_id = mapped_column(
        Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    parent_id = mapped_column(
        Integer, ForeignKey("faq_nodes.id", ondelete="SET NULL"), nullable=True
    )
    question = mapped_column(Text, nullable=False)
    is_final = mapped_column(Boolean, default=False, nullable=False)
    final_answer = mapped_column(Text, nullable=True)
    button_text = mapped_column(String, nullable=True)
    order_index = mapped_column(Integer, default=0, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())

    department: Mapped["Department"] = relationship("Department", back_populates="faq_nodes")
    parent: Mapped["FAQNode | None"] = relationship(
        "FAQNode", remote_side="FAQNode.id", back_populates="children"
    )
    children: Mapped[list["FAQNode"]] = relationship("FAQNode", back_populates="parent")

    # Transient field (not persisted to DB) — used by _attach_depths for tree rendering
    depth: int = 0


class Subscription(Base):
    """Модель подписок студентов на отделы."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "department_id", name="uq_subscription_user_department"),
    )

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    department_id = mapped_column(
        Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="subscriptions")


class Event(Base):
    """Модель мероприятий: афиша отдела и регистрации на них."""

    __tablename__ = "events"

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    department_id = mapped_column(
        Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    title = mapped_column(String, nullable=False)
    description = mapped_column(Text, nullable=True)
    event_date = mapped_column(DateTime(timezone=True), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())

    department: Mapped["Department"] = relationship("Department", back_populates="events")
    registrations: Mapped[list["Registration"]] = relationship(
        "Registration",
        back_populates="event",
        cascade="all, delete-orphan",
    )


class Registration(Base):
    """Регистрация студента на мероприятие (уникальна по паре user/event)."""

    __tablename__ = "registrations"
    __table_args__ = (UniqueConstraint("user_id", "event_id", name="uq_registration_user_event"),)

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    event_id = mapped_column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    registered_at = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="registrations")
    event: Mapped["Event"] = relationship("Event", back_populates="registrations")


class Log(Base):
    __tablename__ = "logs"

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = mapped_column(String, nullable=False)
    details = mapped_column(Text, nullable=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User | None"] = relationship("User", back_populates="logs")


class LoginAttempt(Base):
    """Попытка входа в веб-админку.

    Хранится в БД (не в памяти процесса), чтобы rate limiting переживал
    перезапуски и работал одинаково при нескольких экземплярах панели.
    """

    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_ip_created", "ip", "attempted_at"),)

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    ip = mapped_column(String(64), nullable=False)
    attempted_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    success = mapped_column(Boolean, default=False, nullable=False)


class CrudAttempt(Base):
    """Попытка выполнения CRUD-операции (для многопроцессного rate limiting).

    Аналогично LoginAttempt — хранилище в БД позволяет единый лимит
    при нескольких uvicorn-workers.
    """

    __tablename__ = "crud_attempts"
    __table_args__ = (
        Index("ix_crud_attempts_ip_action_attempted", "ip", "action", "attempted_at"),
    )

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    ip = mapped_column(String(64), nullable=False)
    action = mapped_column(
        String(64), nullable=False, comment="Краткое описание действия (e.g. ticket_status_change)"
    )
    attempted_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class VkOutbox(Base):
    """Сообщения, ожидающие отправки в VK (outbox-паттерн, защита от потери).

    Пишется в той же транзакции, что и бизнес-изменение (например, ответ
    администратора), после чего фоновый воркер доставляет сообщения.
    Этим устраняется «отправили в VK, а в БД не сохранили».
    """

    __tablename__ = "vk_outbox"
    __table_args__ = (Index("ix_vk_outbox_status_created", "status", "created_at"),)

    # Статусы: pending (ожидает доставки) | sent | failed (лимит попыток исчерпан)
    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    vk_id = mapped_column(BigInteger, nullable=False)
    text = mapped_column(Text, nullable=False)
    status = mapped_column(String(16), default="pending", nullable=False)
    attempts = mapped_column(Integer, default=0, nullable=False)
    claimed_at = mapped_column(DateTime(timezone=True), nullable=True)
    error = mapped_column(Text, nullable=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at = mapped_column(DateTime(timezone=True), nullable=True)
