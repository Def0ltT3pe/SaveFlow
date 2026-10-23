from alembic import op

revision = "0002_risk_model"
down_revision = "0001_source_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    -- Единственная строка с действующими настройками модели.
    -- data хранит только то, что меняли; недостающее берётся из значений по умолчанию в коде,
    -- поэтому новый признак не требует миграции.
    CREATE TABLE public.model_settings (
        id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
        data jsonb NOT NULL DEFAULT '{}'::jsonb,
        version integer NOT NULL DEFAULT 1,
        "updatedAt" timestamptz(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    INSERT INTO public.model_settings (id) VALUES (1);

    -- Журнал загрузок PUT /api/v1/source/snapshot.
    CREATE TABLE public.snapshots (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        "loadedAt" timestamptz(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
        "referenceDate" timestamptz(3) NOT NULL,
        accounts integer NOT NULL,
        services integer NOT NULL,
        transactions integer NOT NULL
    );

    -- Изменения статусов услуг между снимками: источник фактов для графика оттока.
    -- Без внешних ключей на services: снимок полностью заменяет услуги, а история остаётся.
    CREATE TABLE public.service_status_history (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        "snapshotId" bigint NOT NULL REFERENCES public.snapshots(id) ON DELETE CASCADE,
        "serviceId" bigint NOT NULL,
        "ownerId" bigint NOT NULL,
        "oldStatus" text,  -- NULL: услуга впервые появилась в снимке
        "newStatus" text,  -- NULL: услуга пропала из снимка
        price bigint NOT NULL,
        "paymentTerm" text NOT NULL,
        "changedAt" timestamptz(3) NOT NULL
    );
    CREATE INDEX service_status_history_changed_idx
        ON public.service_status_history ("changedAt");

    -- Журнал пересчётов модели.
    CREATE TABLE public.risk_runs (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        "computedAt" timestamptz(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
        "asOf" timestamptz(3) NOT NULL,
        "settingsVersion" integer NOT NULL,
        "snapshotId" bigint REFERENCES public.snapshots(id) ON DELETE SET NULL,
        reason text NOT NULL
    );

    -- Оценка каждой услуги. Для deleted/blocked scored = false.
    CREATE TABLE public.service_risk (
        "serviceId" bigint PRIMARY KEY REFERENCES public.services(id) ON DELETE CASCADE,
        "accountId" bigint NOT NULL REFERENCES public.accounts(id) ON DELETE CASCADE,
        scored boolean NOT NULL,
        score numeric(5,2),
        probability numeric(5,4),
        contributions jsonb NOT NULL DEFAULT '[]'::jsonb,
        "daysToExpiry" numeric(10,3) NOT NULL,
        "renewalsInHorizon" integer NOT NULL,
        "monthlyPrice" numeric(14,2) NOT NULL,
        "horizonRevenue" numeric(14,2) NOT NULL,
        "securedByBalance" numeric(14,2) NOT NULL,
        "atRisk" numeric(14,2) NOT NULL,
        "expectedLoss" numeric(14,2) NOT NULL,
        "expectedMonthlyLoss" numeric(14,2) NOT NULL
    );
    CREATE INDEX service_risk_account_idx ON public.service_risk ("accountId");

    -- Оценка клиента. score IS NULL и level = 'none': оценки нет (нет действующих услуг).
    CREATE TABLE public.account_risk (
        "accountId" bigint PRIMARY KEY REFERENCES public.accounts(id) ON DELETE CASCADE,
        score numeric(5,2),
        level text NOT NULL CHECK (level IN ('high', 'medium', 'low', 'none')),
        reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
        "liveServices" integer NOT NULL,
        "monthlyRevenue" numeric(14,2) NOT NULL,
        "horizonRevenue" numeric(14,2) NOT NULL,
        "securedByBalance" numeric(14,2) NOT NULL,
        "atRisk" numeric(14,2) NOT NULL,
        "expectedLoss" numeric(14,2) NOT NULL,
        "expectedMonthlyLoss" numeric(14,2) NOT NULL
    );
    CREATE INDEX account_risk_score_idx ON public.account_risk (score DESC NULLS LAST);
    CREATE INDEX account_risk_level_idx ON public.account_risk (level);
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE public.account_risk;
    DROP TABLE public.service_risk;
    DROP TABLE public.risk_runs;
    DROP TABLE public.service_status_history;
    DROP TABLE public.snapshots;
    DROP TABLE public.model_settings;
    """)
