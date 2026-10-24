from alembic import op

revision = "0006_auth_sessions"
down_revision = "0005_covered_by_bonus"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    -- Сессии входа: переживают перезапуск бэкенда и общие для всех воркеров.
    -- Хранится только sha256 токена: по утёкшей базе войти нельзя. С данными клиентов не связана,
    -- поэтому замена снимка её не трогает.
    CREATE TABLE public.auth_sessions (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        "tokenHash" bytea NOT NULL UNIQUE,
        username text NOT NULL,
        "createdAt" timestamptz(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
        "expiresAt" timestamptz(3) NOT NULL,
        "revokedAt" timestamptz(3)
    );
    CREATE INDEX auth_sessions_expires_idx ON public.auth_sessions ("expiresAt");
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE public.auth_sessions;
    """)
