from alembic import op

revision = "0004_run_settings"
down_revision = "0003_money_unbounded"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    -- Настройки, с которыми посчитан пересчёт: сводка и график берут пороги и горизонт отсюда,
    -- а не из текущих настроек, которые могли поменяться, пока идёт догоняющий пересчёт.
    ALTER TABLE public.risk_runs ADD COLUMN settings jsonb;
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE public.risk_runs DROP COLUMN settings;
    """)
