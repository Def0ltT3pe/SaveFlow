import type {
  AnalyticsSummary,
  ChurnPoint,
  CustomerDetail,
  CustomerListItem,
  ModelSettings,
  ModelSettingsPatch,
  RiskLevel,
  Service,
  ServiceStatus,
} from '../types';

// Форматы ответов бэкенда (app/risk/schemas.py) и перевод их в типы фронта.

type BackendLevel = 'high' | 'medium' | 'low' | 'none';

export const toLevel = (l: BackendLevel): RiskLevel => (l === 'none' ? 'unknown' : l);
export const fromLevel = (l: RiskLevel): BackendLevel => (l === 'unknown' ? 'none' : l);

interface BMoney {
  monthlyRevenue: number;
  horizonRevenue: number;
  securedByBalance: number;
  atRisk: number;
  expectedLoss: number;
  expectedMonthlyLoss: number;
}

interface BReason {
  feature: string;
  title: string;
  text: string;
  points: number | null;
  serviceId: number | null;
  serviceName: string | null;
  services: number;
}

export interface BCustomer {
  id: number;
  email: string;
  balance: number;
  withdrawBalance: number;
  bonusBalance: number;
  bonusState: string;
  totalReplenished: number;
  score: number | null;
  loyalty: number | null;
  level: BackendLevel;
  mainReason: string | null;
  reasons: BReason[];
  liveServices: number;
  money: BMoney;
}

export interface BCustomerPage {
  items: BCustomer[];
  total: number;
}

interface BService {
  id: number;
  name: string;
  status: ServiceStatus;
  paymentTerm: string;
  autoProlong: boolean;
  price: number;
  expiresAt: string;
  expectedLoss: number;
}

export interface BCustomerDetail extends BCustomer {
  services: BService[];
}

export interface BSummary {
  run: { computedAt: string } | null;
  accountsTotal: number;
  accountsByLevel: Record<BackendLevel, number>;
  servicesAtRisk: number;
  horizonDays: number;
  money: BMoney;
  explanation: string;
}

export interface BTimeline {
  points: {
    date: string;
    kind: 'fact' | 'forecast';
    deletions: number;
    estimatedDeletions: number;
    dueServices: number;
    dueAtRisk: number;
    expectedNonRenewals: number;
    churnedClients: number;
    lostRevenue: number;
    clientsAtRisk: number;
    expectedChurnedClients: number;
    revenueAtRisk: number;
    expectedLoss: number;
  }[];
}

export interface BSettings {
  settings: {
    weights: Record<string, number>;
    thresholds: { high: number; medium: number };
    horizonDays: number;
  };
  version: number;
  features: { key: string; title: string; description: string; defaultWeight: number }[];
  defaults: { weights: Record<string, number>; thresholds: { high: number; medium: number } };
}

export const toCustomer = (c: BCustomer): CustomerListItem => ({
  id: c.id,
  email: c.email,
  balance: c.balance,
  totalReplenished: c.totalReplenished,
  score: c.score,
  riskLevel: toLevel(c.level),
  mainReason: c.mainReason,
  expectedLoss: c.money.expectedLoss,
});

const toService = (s: BService): Service => ({
  id: s.id,
  name: s.name,
  status: s.status,
  price: s.price,
  autoprolong: s.autoProlong,
  paymentTerm: s.paymentTerm,
  expiresAt: s.expiresAt,
  expectedLoss: s.expectedLoss,
});

export const toCustomerDetail = (c: BCustomerDetail): CustomerDetail => ({
  ...toCustomer(c),
  bonusBalance: c.bonusBalance,
  withdrawBalance: c.withdrawBalance,
  bonusState: c.bonusState === 'unlocked',
  services: c.services.map(toService),
  factors: c.reasons.map((r) => ({
    key: `${r.feature}:${r.serviceId ?? ''}`,
    label: r.serviceName ? `${r.title} — ${r.serviceName}` : r.title,
    description: r.services > 1 ? `${r.text} (услуг с этим сигналом: ${r.services})` : r.text,
    contribution: r.points ?? 0,
  })),
  revenueAtRisk: c.money.atRisk,
  expectedLoss: c.money.expectedLoss,
});

const toChurnPoint = (p: BTimeline['points'][number]): ChurnPoint => ({
  date: p.date,
  isForecast: p.kind === 'forecast',
  deletedServices: p.deletions,
  estimatedDeletions: p.estimatedDeletions,
  dueServices: p.dueServices,
  atRiskServices: p.dueAtRisk,
  forecastNonRenewals: p.expectedNonRenewals,
  churnedClients: p.churnedClients,
  lostRevenue: p.lostRevenue,
  clientsAtRisk: p.clientsAtRisk,
  expectedChurnedClients: p.expectedChurnedClients,
  revenueAtRisk: p.revenueAtRisk,
  expectedLoss: p.expectedLoss,
});

export const toSummary = (s: BSummary, t: BTimeline): AnalyticsSummary => ({
  customersTotal: s.accountsTotal,
  byLevel: {
    high: s.accountsByLevel.high,
    medium: s.accountsByLevel.medium,
    low: s.accountsByLevel.low,
    unknown: s.accountsByLevel.none,
  },
  servicesAtRisk: s.servicesAtRisk,
  revenueAtRisk: s.money.atRisk,
  expectedLoss: s.money.expectedLoss,
  horizonRevenue: s.money.horizonRevenue,
  horizonDays: s.horizonDays,
  lossExplanation: s.explanation,
  churnSeries: t.points.map(toChurnPoint),
  updatedAt: s.run?.computedAt,
});

export const toSettings = (b: BSettings): ModelSettings => ({
  coefficients: b.features.map((f) => ({
    key: f.key,
    label: f.title,
    description: f.description,
    value: b.settings.weights[f.key] ?? f.defaultWeight,
    defaultValue: b.defaults.weights[f.key] ?? f.defaultWeight,
    min: 0,
    max: 100,
    step: 1,
  })),
  thresholds: b.settings.thresholds,
  defaultThresholds: b.defaults.thresholds,
  horizonDays: b.settings.horizonDays,
});

/** Тело PATCH, возвращающее все ползунки (коэффициенты и пороги) к значениям по умолчанию. */
export const resetToDefaultsPatch = (s: ModelSettings): ModelSettingsPatch => ({
  coefficients: Object.fromEntries(s.coefficients.map((c) => [c.key, c.defaultValue])),
  thresholds: { ...s.defaultThresholds },
});

/** Тело PATCH с текущими значениями всех ползунков: по нему сброс можно отменить. */
export const currentValuesPatch = (s: ModelSettings): ModelSettingsPatch => ({
  coefficients: Object.fromEntries(s.coefficients.map((c) => [c.key, c.value])),
  thresholds: { ...s.thresholds },
});

/** Настройки с ползунками, переставленными по patch, — чтобы показать их, не дожидаясь ответа бэкенда. */
export const withValues = (s: ModelSettings, p: ModelSettingsPatch): ModelSettings => ({
  ...s,
  coefficients: s.coefficients.map((c) => ({ ...c, value: p.coefficients?.[c.key] ?? c.value })),
  thresholds: { ...s.thresholds, ...p.thresholds },
});

/** Стоят ли все ползунки на значениях по умолчанию. */
export const isAtDefaults = (s: ModelSettings): boolean =>
  s.coefficients.every((c) => c.value === c.defaultValue) &&
  s.thresholds.high === s.defaultThresholds.high && s.thresholds.medium === s.defaultThresholds.medium;

export const fromSettingsPatch = (p: ModelSettingsPatch) => {
  const body: Record<string, unknown> = {};
  if (p.coefficients && Object.keys(p.coefficients).length) body.weights = p.coefficients;
  if (p.thresholds && Object.keys(p.thresholds).length) body.thresholds = p.thresholds;
  if (p.horizonDays !== undefined) body.horizonDays = p.horizonDays;
  return body;
};
