"""pgbouncer.get_auth: прозрачная авторизация через PostgreSQL (SEC-12)

Revision ID: b4c5d6e7f8a
Revises: a3b4c5d6e7f8
Create Date: 2026-09-25 12:00:00.000000

Создаёт схему ``pgbouncer`` и SECURITY DEFINER-функцию ``get_auth(TEXT)``,
которая возвращает пару (usename, passwd) для роли с аргумента.

Назначение
----------
PgBouncer умеет проверять клиента по ``auth_query`` — SQL-запросу к
PostgreSQL, который возвращает SCRAM-SHA-256 verifier. Это избавляет от
хранения паролей всех пользователей БД в ``userlist.txt``: достаточно
одной служебной роли (``auth_user``), от имени которой выполняется запрос.

Почему SECURITY DEFINER
-----------------------
``pg_authid`` (и его представление ``pg_shadow``) доступны только
суперпользователям. Функция объявлена ``SECURITY DEFINER``, поэтому
выполняется с правами владельца (роли, запустившей миграцию) и позволяет
PgBouncer узнать verifier любого пользователя, не выдавая ему права
суперпользователя.

Безопасность
------------
* ``SET search_path = pg_catalog`` — защита от подмены объектов через
  злонамеренный ``search_path`` вызывающей стороны (CVE-2018-1058-подобный
  вектор). Все обращения в теле функции полностьюqualified.
* ``REVOKE ALL ... FROM PUBLIC`` — функция недоступна анонимным ролям;
  доступ выдаётся явно (см. ``_grant_to_auth_user``).
* Возвращается только verifier, а не сам пароль: значения функции
  необратимы, поэтому даже при утечке логов восстановить пароль нельзя.
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f8a"
down_revision: str | Sequence[str] | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Имя функции в канонической, полностьюqualified-форме. Совпадает с
# auth_query в pgbouncer/pgbouncer.ini.
GET_AUTH_FUNCTION = "pgbouncer.get_auth(TEXT)"


def _get_auth_user(connection) -> str | None:
    """Определить роль, от имени которой PgBouncer выполняет auth_query.

    По умолчанию берётся текущий пользователь соединения миграции — это
    и есть ``POSTGRES_USER`` (владелец базы), которому в docker-compose
    передан ``AUTH_USER``. Если служебная роль другая, достаточно
    передать ``-x auth_user=<роль>`` в ``alembic upgrade``.
    """
    try:
        from alembic import context as alembic_context
    except ImportError:  # pragma: no cover - alembic всегда доступен
        alembic_context = None

    if alembic_context is not None:
        override = alembic_context.get_x_argument(as_dictionary=True).get("auth_user")
        if override:
            return override

    from sqlalchemy import text

    return connection.scalar(text("SELECT current_user"))


def _grant_to_auth_user(connection, auth_user: str) -> None:
    """Выдать минимальные права на схему и функцию роли auth_user."""
    from sqlalchemy import text

    # Идентификатор роли подставляется только через параметр :role —
    # защита от SQL-инъекции, если роль задана через -x auth_user.
    connection.execute(text("GRANT USAGE ON SCHEMA pgbouncer TO :role"), {"role": auth_user})
    connection.execute(
        text("GRANT EXECUTE ON FUNCTION pgbouncer.get_auth(TEXT) TO :role"),
        {"role": auth_user},
    )


def upgrade() -> None:
    """Создать схему pgbouncer и функцию get_auth(TEXT)."""
    from sqlalchemy import text

    from alembic import op

    # Alembic даёт доступ к текущему соединению через op.get_bind().
    connection = op.get_bind()

    connection.execute(text("CREATE SCHEMA IF NOT EXISTS pgbouncer"))

    # SECURITY DEFINER + фиксированный search_path: тело функции
    # полностью квалифицировано (pg_catalog.*), поэтому подмена
    # объектов через search_path вызывающей стороны невозможна.
    connection.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION pgbouncer.get_auth(p_usename TEXT)
            RETURNS TABLE(usename TEXT, passwd TEXT)
            LANGUAGE sql
            STABLE
            SECURITY DEFINER
            SET search_path = pg_catalog
            AS $$
                SELECT a.rolname::TEXT, a.rolpassword::TEXT
                FROM pg_catalog.pg_authid a
                WHERE a.rolname = p_usename
            $$
            """
        )
    )

    # Комментарий фиксирует назначение функции в psql и в каталоге БД.
    connection.execute(
        text(
            "COMMENT ON FUNCTION pgbouncer.get_auth(TEXT) IS "
            "'SEC-12: выдаёт PgBouncer SCRAM-SHA-256 verifier роли для auth_query'"
        )
    )

    # По умолчанию функция недоступна PUBLIC — доступ только явно
    # выданной служебной роли.
    connection.execute(text("REVOKE ALL ON FUNCTION pgbouncer.get_auth(TEXT) FROM PUBLIC"))

    auth_user = _get_auth_user(connection)
    if auth_user:
        _grant_to_auth_user(connection, auth_user)


def downgrade() -> None:
    """Удалить функцию get_auth(TEXT) и схему pgbouncer."""
    from sqlalchemy import text

    from alembic import op

    connection = op.get_bind()
    connection.execute(text("DROP FUNCTION IF EXISTS pgbouncer.get_auth(TEXT)"))
    connection.execute(text("DROP SCHEMA IF EXISTS pgbouncer"))
