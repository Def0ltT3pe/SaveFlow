from alembic import op

revision = "0005_covered_by_bonus"
down_revision = "0004_run_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    -- Часть продлений, которую автопродление оплатит доступными бонусами: она не под угрозой,
    -- но и не выручка реальными деньгами, поэтому хранится отдельно от securedByBalance.
    ALTER TABLE public.service_risk ADD COLUMN "coveredByBonus" numeric NOT NULL DEFAULT 0;
    ALTER TABLE public.account_risk ADD COLUMN "coveredByBonus" numeric NOT NULL DEFAULT 0;
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE public.service_risk DROP COLUMN "coveredByBonus";
    ALTER TABLE public.account_risk DROP COLUMN "coveredByBonus";
    """)
