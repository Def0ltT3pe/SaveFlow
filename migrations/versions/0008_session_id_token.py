from alembic import op

revision = "0008_session_id_token"
down_revision = "0007_oidc_login_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    -- id_token сессии, открытой через OIDC: с ним провайдер при выходе не спрашивает «Выйти?».
    -- У входа по логину и паролю — NULL.
    ALTER TABLE public.auth_sessions ADD COLUMN "idToken" text;
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE public.auth_sessions DROP COLUMN "idToken";
    """)
