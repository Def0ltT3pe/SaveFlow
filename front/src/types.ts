// ВСЕ интерфейсы фронта. Если бэкенд отдаёт другие имена полей — правится только здесь и в api/client.ts.

export type RiskLevel = 'high' | 'medium' | 'low' | 'unknown';

export type ServiceStatus = 'active' | 'suspended' | 'prolong_wait' | 'deleted' | 'blocked';

/** Все суммы — в еврокопейках (100 = 1 €). */
export type Cents = number;

// ── Авторизация ─────────────────────────────────────────
export interface User {
  id: string | number;
  username: string;
  role?: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

// ── Клиенты ─────────────────────────────────────────────
export type CustomerSortField = 'id' | 'email' | 'balance' | 'totalReplenished' | 'score';
export type SortOrder = 'asc' | 'desc';

export interface CustomersQuery {
  sortBy?: CustomerSortField;
  order?: SortOrder;
  riskLevel?: RiskLevel;
  search?: string;
}

export interface CustomerListItem {
  id: string | number;
  email: string;
  balance: Cents;
  totalReplenished: Cents;
  /** null — данных недостаточно, оценки нет */
  score: number | null;
  riskLevel: RiskLevel;
  /** Короткая главная причина для таблицы */
  mainReason?: string | null;
  expectedLoss?: Cents;
}

export interface Service {
  id: string | number;
  name: string;
  status: ServiceStatus;
  price: Cents;
  autoprolong: boolean;
  paymentTerm: string; // hour | week | month | quarter_year | half_year | year
  expiresAt?: string | null;
  /** вклад услуги в денежную оценку, если бэкенд считает по услугам */
  expectedLoss?: Cents;
}

export interface RiskFactor {
  key: string;
  label: string;
  /** Человекочитаемое объяснение: «Автопродление выключено, услуга истекает через 2 дня» */
  description: string;
  /** Вклад в итоговый score (в пунктах) */
  contribution: number;
  /** Вес, с которым признак вошёл в формулу */
  weight?: number;
}

export interface CustomerDetail extends CustomerListItem {
  bonusBalance: Cents;
  withdrawBalance: Cents;
  bonusState?: boolean;
  services: Service[];
  factors: RiskFactor[];
  /** Общее текстовое объяснение оценки */
  explanation?: string;
  /** Сколько денег клиента под угрозой (стоимость услуг, которые могут не продлиться) */
  revenueAtRisk?: Cents;
  /** revenueAtRisk × вероятность оттока */
  expectedLoss: Cents;
}

// ── Аналитика ───────────────────────────────────────────
export interface ChurnPoint {
  date: string; // YYYY-MM-DD (UTC)
  isForecast: boolean;
  /** факт: услуги, перешедшие в deleted между снимками */
  deletedServices: number;
  /** факт, но дата оценена: удалённые услуги без истории (дата истечения + льготный период) */
  estimatedDeletions: number;
  /** прогноз: все услуги с ближайшим продлением в этот день */
  dueServices: number;
  /** прогноз: из них услуги под риском (score не ниже порога среднего риска) */
  atRiskServices: number;
  /** прогноз: ожидаемые непродления — сумма вероятностей ухода */
  forecastNonRenewals: number;
}

export interface AnalyticsSummary {
  customersTotal: number;
  byLevel: Record<RiskLevel, number>;
  servicesAtRisk: number;
  /** Стоимость услуг, которым грозит непродление */
  revenueAtRisk: Cents;
  /** Ожидаемые потери с учётом вероятности */
  expectedLoss: Cents;
  /** Вся выручка от продлений за горизонт прогноза: база для доли потерь */
  horizonRevenue: Cents;
  horizonDays: number;
  /** Объяснение смысла денежной оценки (формула простыми словами) */
  lossExplanation?: string;
  churnSeries?: ChurnPoint[];
  updatedAt?: string;
}

// ── Настройки модели ────────────────────────────────────
export interface Coefficient {
  key: string;
  label: string;
  description?: string;
  value: number;
  /** значение по умолчанию: к нему возвращает кнопка сброса */
  defaultValue: number;
  min: number;
  max: number;
  step?: number;
}

export interface ModelSettings {
  coefficients: Coefficient[];
  thresholds: { medium: number; high: number };
  defaultThresholds: { medium: number; high: number };
  /** сколько часов/дней смотрим вперёд, если бэкенд это поддерживает */
  horizonDays?: number;
}

export interface ModelSettingsPatch {
  coefficients?: Record<string, number>;
  thresholds?: Partial<ModelSettings['thresholds']>;
  horizonDays?: number;
}

// ── Снимок исходных данных (PUT /source/snapshot) ───────
export interface SourceSnapshot {
  accounts: unknown[];
  services: unknown[];
  transactions: unknown[];
}
