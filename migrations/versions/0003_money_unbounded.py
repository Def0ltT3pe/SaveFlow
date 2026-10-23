from alembic import op

revision = "0003_money_unbounded"
down_revision = "0002_risk_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    -- numeric(14,2) переполнялся на дорогих почасовых услугах: выручка за горизонт = цена × до 8761 продлений.
    ALTER TABLE public.service_risk
        ALTER COLUMN "monthlyPrice" TYPE numeric,
        ALTER COLUMN "horizonRevenue" TYPE numeric,
        ALTER COLUMN "securedByBalance" TYPE numeric,
        ALTER COLUMN "atRisk" TYPE numeric,
        ALTER COLUMN "expectedLoss" TYPE numeric,
        ALTER COLUMN "expectedMonthlyLoss" TYPE numeric;
    ALTER TABLE public.account_risk
        ALTER COLUMN "monthlyRevenue" TYPE numeric,
        ALTER COLUMN "horizonRevenue" TYPE numeric,
        ALTER COLUMN "securedByBalance" TYPE numeric,
        ALTER COLUMN "atRisk" TYPE numeric,
        ALTER COLUMN "expectedLoss" TYPE numeric,
        ALTER COLUMN "expectedMonthlyLoss" TYPE numeric;
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE public.service_risk
        ALTER COLUMN "monthlyPrice" TYPE numeric(14,2),
        ALTER COLUMN "horizonRevenue" TYPE numeric(14,2),
        ALTER COLUMN "securedByBalance" TYPE numeric(14,2),
        ALTER COLUMN "atRisk" TYPE numeric(14,2),
        ALTER COLUMN "expectedLoss" TYPE numeric(14,2),
        ALTER COLUMN "expectedMonthlyLoss" TYPE numeric(14,2);
    ALTER TABLE public.account_risk
        ALTER COLUMN "monthlyRevenue" TYPE numeric(14,2),
        ALTER COLUMN "horizonRevenue" TYPE numeric(14,2),
        ALTER COLUMN "securedByBalance" TYPE numeric(14,2),
        ALTER COLUMN "atRisk" TYPE numeric(14,2),
        ALTER COLUMN "expectedLoss" TYPE numeric(14,2),
        ALTER COLUMN "expectedMonthlyLoss" TYPE numeric(14,2);
    """)
