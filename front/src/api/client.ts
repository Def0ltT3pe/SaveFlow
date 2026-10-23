import axios from 'axios';
import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  CustomerListItem,
  CustomersQuery,
  LoginRequest,
  ModelSettingsPatch,
  SourceSnapshot,
  User,
} from '../types';
import {
  type BCustomerDetail,
  type BCustomerPage,
  type BSettings,
  type BSummary,
  type BTimeline,
  fromLevel,
  fromSettingsPatch,
  toCustomer,
  toCustomerDetail,
  toSettings,
  toSummary,
} from './backend';

// ── Axios ───────────────────────────────────────────────
// Сессия живёт в httpOnly-cookie; если бэкенд отдаёт токен — кладём его в localStorage('token').
export const http = axios.create({ baseURL: '/api/v1', withCredentials: true });

http.interceptors.request.use((cfg) => {
  const token = localStorage.getItem('token');
  if (token) cfg.headers.Authorization = `Bearer ${token}`;
  return cfg;
});

http.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) window.dispatchEvent(new Event('auth:unauthorized'));
    return Promise.reject(err);
  },
);

/** Понятное сообщение об ошибке для UI. */
export function errorMessage(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const d = e.response?.data as { detail?: unknown; message?: string } | undefined;
    if (typeof d?.detail === 'string') return d.detail;
    if (d?.message) return d.message;
    if (e.response) return `Ошибка сервера (${e.response.status})`;
    return 'Нет связи с сервером';
  }
  return e instanceof Error ? e.message : 'Неизвестная ошибка';
}

// ── Авторизация ─────────────────────────────────────────
export const login = async (body: LoginRequest) => {
  const { data } = await http.post<{ access_token: string; token_type: string }>('/auth/login', body);
  localStorage.setItem('token', data.access_token);
  return data;
};
export const getMe = async (): Promise<User> => {
  const { data } = await http.get<{ username: string }>('/auth/me');
  return { id: data.username, username: data.username };
};
export const logout = async () => {
  try {
    await http.post('/auth/logout');
  } finally {
    localStorage.removeItem('token');
  }
};

// ── Клиенты ─────────────────────────────────────────────
const PAGE_LIMIT = 500;

export const getCustomers = async (q: CustomersQuery = {}): Promise<CustomerListItem[]> => {
  const params: Record<string, string | number> = { limit: PAGE_LIMIT };
  if (q.sortBy) params.sort = q.sortBy;
  if (q.order) params.order = q.order;
  if (q.riskLevel) params.level = fromLevel(q.riskLevel);
  if (q.search) params.search = q.search;
  const { data } = await http.get<BCustomerPage>('/customers', { params });
  return data.items.map(toCustomer);
};
export const getCustomer = async (id: string | number) =>
  toCustomerDetail((await http.get<BCustomerDetail>(`/customers/${id}`)).data);

// ── Аналитика ───────────────────────────────────────────
export const getSummary = async () => {
  const [summary, timeline] = await Promise.all([
    http.get<BSummary>('/analytics/summary'),
    http.get<BTimeline>('/analytics/churn-timeline'),
  ]);
  return toSummary(summary.data, timeline.data);
};

// ── Настройки модели ────────────────────────────────────
export const getSettings = async () => toSettings((await http.get<BSettings>('/model/settings')).data);
export const patchSettings = async (patch: ModelSettingsPatch) =>
  toSettings((await http.patch<BSettings>('/model/settings', fromSettingsPatch(patch))).data);

// ── Снимок данных ───────────────────────────────────────
export const putSnapshot = async (snapshot: SourceSnapshot) =>
  (await http.put('/source/snapshot', snapshot)).data;

// ── Живое обновление ────────────────────────────────────
// Данные перезапрашиваются: (1) по таймеру, (2) сразу после любого изменения из UI (emitChanged),
// (3) при возвращении на вкладку, (4) по SSE-событию бэкенда после каждого пересчёта модели.
const CHANGED = 'data:changed';
export const emitChanged = () => window.dispatchEvent(new Event(CHANGED));

/** Подписка на SSE /events/stream: любое изменение данных или настроек → emitChanged(). */
export function useServerEvents() {
  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) return;
    const source = new EventSource(`/api/v1/events/stream?token=${encodeURIComponent(token)}`);
    let first = true;
    source.addEventListener('update', () => {
      if (first) first = false;
      else emitChanged();
    });
    return () => source.close();
  }, []);
}

export function useLive<T>(fetcher: () => Promise<T>, deps: unknown[] = [], intervalMs = 5000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const seq = useRef(0);

  const reload = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const result = await fetcherRef.current();
      if (mine !== seq.current) return; // пришёл устаревший ответ
      setData(result);
      setError(null);
      setUpdatedAt(new Date());
    } catch (e) {
      if (mine === seq.current) setError(errorMessage(e));
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    reload();
    const tick = () => document.visibilityState === 'visible' && reload();
    const timer = intervalMs > 0 ? window.setInterval(tick, intervalMs) : undefined;
    window.addEventListener(CHANGED, reload);
    document.addEventListener('visibilitychange', tick);
    return () => {
      if (timer) window.clearInterval(timer);
      window.removeEventListener(CHANGED, reload);
      document.removeEventListener('visibilitychange', tick);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload, intervalMs, ...deps]);

  return { data, error, loading, updatedAt, reload };
}
