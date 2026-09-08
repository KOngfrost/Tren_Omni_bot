import enum

import bleach
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()

# Разрешённые теги для bleach: пустой список = удалить все теги.
# Это обеспечивает максимальную безопасность — пользовательский ввод
# хранится как чистый текст без HTML-разметки.
_SANITIZE_ALLOWED_TAGS: list[str] = []
_SANITIZE_ALLOWED_ATTRIBUTES: dict[str, list[str]] = {}


# ==========================================
# Автоматическая XSS-санитизация на уровне моделей
# ==========================================
# Event listeners, которые автоматически санитизируют пользовательский ввод
# перед записью в БД. Это проактивная защита, дополняющая экранирование
# в Jinja2-шаблонах.


def sanitize_xss(value: str) -> str:
    """Автоматическая санитизация XSS-паттернов в строке.

    Использует bleach для удаления всех HTML-тегов и атрибутов.
    В отличие от regex-подхода, bleach корректно обрабатывает вложенные
    теги, сущности и edge-кейсы.
    """
    if not value or not isinstance(value, str):
        return value

    try:
        return bleach.clean(
            value,
            tags=_SANITIZE_ALLOWED_TAGS,
            attributes=_SANITIZE_ALLOWED_ATTRIBUTES,
            strip=True,
        )
    except Exception:
        # Fallback: strip tags вручную
        import re
        return re.sub(r'<[^>]+>', '', value)


@event.listens_for(Text, "before_insert", propagate=True)
@event.listens_for(Text, "before_update", propagate=True)
def sanitize_text_columns(target, value, oldvalue, initiator):
    """Автоматическая санитизация всех Text-колонок перед записью.

    Применяется ко всем моделям, использующим Text-колонки:
    - Ticket.description
    - Ticket.topic
    - Ticket.response_text
    - TicketMessage.message
    - Log.details
    и др.
    """
    # Получаем имя колонки из initiator
    if initiator is not None:
        col_name = initiator.key
        # Санитизируем только текстовые поля с пользовательским вводом
        if col_name in ("description", "topic", "message", "response_text",
                        "details", "answer", "question", "final_answer",
                        "keywords", "title", "button_text"):
            sanitized = sanitize_xss(target.__dict__.get(col_name))
            if sanitized != target.__dict__.get(col_name):
                target.__dict__[col_name] = sanitized


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    SUPERADMIN = "superadmin"


class WebRole(str, enum.Enum):
    """Роли пользователей веб-админки."""
    SUPERADMIN = "SUPERADMIN"
    DEPARTMENT_ADMIN = "DEPARTMENT_ADMIN"
    VIEWER = "VIEWER"


class MessageAuthorType(str, enum.Enum):
    """Автор сообщения в истории заявки."""
    USER = "user"        # студент (VK)
    ADMIN = "admin"      # администратор (веб-панель)
    SYSTEM = "system"    # системные события (смена статуса и т.п.)


class TicketStatus(str, enum.Enum):
    NEW = "Новое"
    IN_PROGRESS = "В обработке"
    TRANSFERRED_ADMIN = "Передано в администрацию СГ"
    TRANSFERRED_HOUSEKEEPING = "Передано в Хозчасть"
    COMPLETED = "Выполнено"
    COMPLETED_AUTO = "Выполнено (авто)"
    ANONYMOUS = "Анонимное"


class Department(Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    admins: Mapped[list["Admin"]] = relationship("Admin", back_populates="department")
    tickets: Mapped[list["Ticket"]] = relationship("Ticket", back_populates="department")
    knowledge_base: Mapped[list["KnowledgeBase"]] = relationship(
        "KnowledgeBase", back_populates="department"
    )
    faq_nodes: Mapped[list["FAQNode"]] = relationship("FAQNode", back_populates="department")
    events: Mapped[list["Event"]] = relationship("Event", back_populates="department")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    vk_id = Column(Integer, unique=True, nullable=False)
    full_name = Column(String, nullable=True)
    dormitory = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

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

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="SET NULL"), nullable=True)
    role = Column(SAEnum(UserRole), default=UserRole.ADMIN)

    user: Mapped["User"] = relationship("User")
    department: Mapped["Department | None"] = relationship(
        "Department", back_populates="admins"
    )
    web_user: Mapped["WebUser | None"] = relationship("WebUser", back_populates="admin")


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        Index("ix_tickets_user_id", "user_id"),
        Index("ix_tickets_department_id", "department_id"),
        Index("ix_tickets_status", "status"),
        Index("ix_tickets_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="SET NULL"), nullable=True)
    topic = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    status = Column(SAEnum(TicketStatus), default=TicketStatus.NEW, nullable=False)
    response_text = Column(Text, nullable=True)
    is_anonymous = Column(Boolean, default=False, nullable=False)
    auto_closed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User | None"] = relationship("User", back_populates="tickets")
    department: Mapped["Department | None"] = relationship(
        "Department", back_populates="tickets"
    )
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

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(
        Integer,
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_type = Column(SAEnum(MessageAuthorType), nullable=False)
    author_vk_id = Column(Integer, nullable=True)  # VK ID автора, если это студент
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="messages")


class WebUser(Base):
    """Пользователь веб-админки (пароль хранится только в виде хеша)."""
    __tablename__ = "web_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)  # формат: pbkdf2_sha256$iterations$salt$hash
    role = Column(SAEnum(WebRole), default=WebRole.VIEWER, nullable=False)
    admin_id = Column(Integer, ForeignKey("admins.id", ondelete="CASCADE"), nullable=True)
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="SET NULL"), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    department: Mapped["Department | None"] = relationship("Department")
    admin: Mapped["Admin | None"] = relationship("Admin")


class ReportRun(Base):
    """Факт отправки ежедневного отчёта — защита от повторных отправок."""
    __tablename__ = "report_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_date = Column(Date, unique=True, nullable=False)  # дата отчёта (за которую он сформирован)
    status = Column(String, default="sent", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class KnowledgeBase(Base):
    """Модель базы знаний: ключевые слова → заготовленный ответ отдела."""

    __tablename__ = "knowledge_base"

    id = Column(Integer, primary_key=True, autoincrement=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=False)
    keywords = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

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

    id = Column(Integer, primary_key=True, autoincrement=True)
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False)
    parent_id = Column(Integer, ForeignKey("faq_nodes.id", ondelete="SET NULL"), nullable=True)
    question = Column(Text, nullable=False)
    is_final = Column(Boolean, default=False, nullable=False)
    final_answer = Column(Text, nullable=True)
    button_text = Column(String, nullable=True)
    order_index = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

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

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "department_id", name="uq_subscription_user_department"),)

    user: Mapped["User"] = relationship("User", back_populates="subscriptions")


class Event(Base):
    """Модель мероприятий: афиша отдела и регистрации на них."""

    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    event_date = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    department: Mapped["Department"] = relationship("Department", back_populates="events")
    registrations: Mapped[list["Registration"]] = relationship(
        "Registration",
        back_populates="event",
        cascade="all, delete-orphan",
    )


class Registration(Base):
    """Регистрация студента на мероприятие (уникальна по паре user/event)."""

    __tablename__ = "registrations"
    __table_args__ = (
        UniqueConstraint("user_id", "event_id", name="uq_registration_user_event"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    registered_at = Column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="registrations")
    event: Mapped["Event"] = relationship("Event", back_populates="registrations")


class Log(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String, nullable=False)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User | None"] = relationship("User", back_populates="logs")


class LoginAttempt(Base):
    """Попытка входа в веб-админку.

    Хранится в БД (не в памяти процесса), чтобы rate limiting переживал
    перезапуски и работал одинаково при нескольких экземплярах панели.
    """

    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_ip_created", "ip", "attempted_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    ip = Column(String(64), nullable=False)
    attempted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    success = Column(Boolean, default=False, nullable=False)


class VkOutbox(Base):
    """Сообщения, ожидающие отправки в VK (outbox-паттерн, защита от потери).

    Пишется в той же транзакции, что и бизнес-изменение (например, ответ
    администратора), после чего фоновый воркер доставляет сообщения.
    Этим устраняется «отправили в VK, а в БД не сохранили».
    """

    __tablename__ = "vk_outbox"
    __table_args__ = (
        Index("ix_vk_outbox_status_created", "status", "created_at"),
    )

    # Статусы: pending (ожидает доставки) | sent | failed (лимит попыток исчерпан)
    id = Column(Integer, primary_key=True, autoincrement=True)
    vk_id = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    status = Column(String(16), default="pending", nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    sent_at = Column(DateTime(timezone=True), nullable=True)
