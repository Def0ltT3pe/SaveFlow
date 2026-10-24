import type { ChurnPoint } from '../types';

/** Строка графика оттока: факт заполнен для прошлых дней, прогноз — для будущих, опорный день — оба. */
export interface ChartRow {
  date: string;
  deleted: number | null;
  estimated: number | null;
  atRisk: number | null;
  forecast: number | null;
}

/**
 * Точки бэкенда → строки графика, по одной на дату.
 * Опорная дата приходит дважды (последний день факта и первый день прогноза); на оси категорий
 * повтор даты ломает привязку ReferenceLine, поэтому такие точки склеиваются в одну строку.
 */
export function toChartData(series: ChurnPoint[]): { rows: ChartRow[]; forecastStart: string | null } {
  const rows: ChartRow[] = [];
  const byDate = new Map<string, ChartRow>();
  for (const p of series) {
    let row = byDate.get(p.date);
    if (!row) {
      row = { date: p.date, deleted: null, estimated: null, atRisk: null, forecast: null };
      byDate.set(p.date, row);
      rows.push(row);
    }
    if (p.isForecast) {
      row.atRisk = p.atRiskServices;
      row.forecast = p.forecastNonRenewals;
    } else {
      row.deleted = p.deletedServices;
      row.estimated = p.estimatedDeletions;
    }
  }
  rows.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  const forecastStart = series.find((p) => p.isForecast)?.date ?? null;
  return { rows, forecastStart };
}

/** Ширина на один день графика: при 62 днях это ~800 px, на узком экране график прокручивается вбок. */
export const DAY_WIDTH_PX = 12;
const AXIS_WIDTH_PX = 60;

export const chartMinWidth = (days: number) => days * DAY_WIDTH_PX + AXIS_WIDTH_PX;

/** Прокрутка, при которой день с индексом index (начало прогноза) оказывается в середине видимой области. */
export function forecastScrollLeft(index: number, days: number, scrollWidth: number, clientWidth: number): number {
  const max = Math.max(scrollWidth - clientWidth, 0);
  if (index < 0 || days === 0) return max; // прогноза нет — показываем последние дни
  const x = AXIS_WIDTH_PX + ((index + 0.5) / days) * (scrollWidth - AXIS_WIDTH_PX);
  return Math.min(Math.max(x - clientWidth / 2, 0), max);
}

/** «2026-10-24» как календарный день, без сдвига часового пояса браузера. */
export function parseDay(day: string): Date {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d);
}

export const shortDay = (day: string) => parseDay(day).toLocaleDateString('ru-RU', { day: '2-digit', month: 'short' });
export const fullDay = (day: string) => parseDay(day).toLocaleDateString('ru-RU');
