from alembic import op

revision = "0007_oidc_login_requests"
down_revision = "0006_auth_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    -- Начатые входы через OIDC: между переходом к провайдеру и возвратом от него.
    -- Запись одноразовая и живёт 10 минут; state хранится только как sha256.
    CREATE TABLE public.oidc_login_requests (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        "stateHash" bytea NOT NULL UNIQUE,
        nonce text NOT NULL,
        "codeVerifier" text NOT NULL,
        "createdAt" timestamptz(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
        "expiresAt" timestamptz(3) NOT NULL
    );
    CREATE INDEX oidc_login_requests_expires_idx ON public.oidc_login_requests ("expiresAt");
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE public.oidc_login_requests;
    """)
