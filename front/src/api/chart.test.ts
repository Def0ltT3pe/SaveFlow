// Запуск: npm test (node --experimental-strip-types --test), без сторонних библиотек
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { DAY_WIDTH_PX, chartMinWidth, forecastScrollLeft, forecastTotals, parseDay, toChartData } from './chart.ts';
import type { ChurnPoint } from '../types.ts';

const point = (date: string, isForecast: boolean, extra: Partial<ChurnPoint> = {}): ChurnPoint => ({
  date, isForecast, deletedServices: 0, estimatedDeletions: 0, dueServices: 0, atRiskServices: 0, forecastNonRenewals: 0,
  churnedClients: 0, lostRevenue: 0, clientsAtRisk: 0, expectedChurnedClients: 0, revenueAtRisk: 0, expectedLoss: 0, ...extra,
});
const fact = (date: string, extra: Partial<ChurnPoint> = {}) => point(date, false, extra);
const forecast = (date: string, extra: Partial<ChurnPoint> = {}) => point(date, true, extra);

test('опорная дата из факта и прогноза склеивается в одну строку', () => {
  const { rows, forecastStart } = toChartData([
    fact('2026-10-23', { churnedClients: 1 }),
    fact('2026-10-24', { churnedClients: 2, lostRevenue: 1500, deletedServices: 2, estimatedDeletions: 1 }),
    forecast('2026-10-24', { clientsAtRisk: 3, expectedChurnedClients: 2.5, revenueAtRisk: 4000, expectedLoss: 2100,
                             dueServices: 5, atRiskServices: 3 }),
    forecast('2026-10-25', { clientsAtRisk: 1 }),
  ]);
  assert.deepEqual(rows.map((r) => r.date), ['2026-10-23', '2026-10-24', '2026-10-25']);
  assert.equal(new Set(rows.map((r) => r.date)).size, rows.length, 'даты на оси не повторяются');
  assert.equal(forecastStart, '2026-10-24');
  assert.deepEqual(rows[1], {
    date: '2026-10-24', lostClients: 2, lostRevenue: 1500, deleted: 2, estimated: 1,
    riskClients: 3, expectedClients: 2.5, revenueAtRisk: 4000, expectedLoss: 2100, dueServices: 5, atRiskServices: 3,
  });
});

test('факт и прогноз не смешиваются: в прошлом нет прогноза, в будущем нет ушедших', () => {
  const { rows } = toChartData([
    fact('2026-10-23', { churnedClients: 4, lostRevenue: 900 }),
    forecast('2026-10-25', { clientsAtRisk: 2, expectedLoss: 700 }),
  ]);
  assert.equal(rows[0].lostClients, 4);
  assert.equal(rows[0].riskClients, null);
  assert.equal(rows[0].expectedLoss, null);
  assert.equal(rows[1].riskClients, 2);
  assert.equal(rows[1].lostClients, null);
  assert.equal(rows[1].lostRevenue, null);
});

test('итог прогноза — сумма по дням, факт в него не входит', () => {
  const { rows } = toChartData([
    fact('2026-10-24', { churnedClients: 5, lostRevenue: 99_999 }),
    forecast('2026-10-24', { clientsAtRisk: 2, expectedChurnedClients: 1.25, revenueAtRisk: 3000, expectedLoss: 1200 }),
    forecast('2026-10-26', { clientsAtRisk: 1, expectedChurnedClients: 0.5, revenueAtRisk: 1000, expectedLoss: 300 }),
  ]);
  assert.deepEqual(forecastTotals(rows), { riskClients: 3, expectedClients: 1.75 });
  assert.deepEqual(forecastTotals([]), { riskClients: 0, expectedClients: 0 });
});

test('строки идут по датам, даже если точки пришли не по порядку', () => {
  const { rows } = toChartData([forecast('2026-10-26'), fact('2026-10-24'), forecast('2026-10-25')]);
  assert.deepEqual(rows.map((r) => r.date), ['2026-10-24', '2026-10-25', '2026-10-26']);
});

test('пустой ряд и ряд без прогноза', () => {
  assert.deepEqual(toChartData([]), { rows: [], forecastStart: null });
  assert.equal(toChartData([fact('2026-10-24')]).forecastStart, null);
});

test('минимальная ширина растёт с числом дней: на 62 дня график шире узкой панели', () => {
  assert.equal(chartMinWidth(62) - chartMinWidth(61), DAY_WIDTH_PX);
  assert.ok(chartMinWidth(62) > 550);
});

test('прокрутка ставит начало прогноза в середину видимой области', () => {
  const days = 62, scrollWidth = chartMinWidth(days), clientWidth = 550;
  const left = forecastScrollLeft(30, days, scrollWidth, clientWidth);
  const lineX = 60 + (30.5 / days) * (scrollWidth - 60);
  assert.ok(Math.abs(left + clientWidth / 2 - lineX) < 1);
});

test('прокрутка не выходит за края', () => {
  assert.equal(forecastScrollLeft(0, 62, 800, 550), 0);
  assert.equal(forecastScrollLeft(61, 62, 800, 550), 250);
  assert.equal(forecastScrollLeft(-1, 62, 800, 550), 250); // без прогноза — последние дни
  assert.equal(forecastScrollLeft(30, 62, 500, 550), 0); // всё помещается — прокрутки нет
});

test('дата разбирается как календарный день без сдвига часового пояса', () => {
  const d = parseDay('2026-10-24');
  assert.deepEqual([d.getFullYear(), d.getMonth(), d.getDate()], [2026, 9, 24]);
});
