// Запуск: npm test (node --experimental-strip-types --test), без сторонних библиотек
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { DAY_WIDTH_PX, chartMinWidth, forecastScrollLeft, parseDay, toChartData } from './chart.ts';

const fact = (date: string, deleted = 0, estimated = 0) => ({
  date, isForecast: false, deletedServices: deleted, estimatedDeletions: estimated,
  dueServices: 0, atRiskServices: 0, forecastNonRenewals: 0,
});
const forecast = (date: string, due = 0, atRisk = 0, expected = 0) => ({
  date, isForecast: true, deletedServices: 0, estimatedDeletions: 0,
  dueServices: due, atRiskServices: atRisk, forecastNonRenewals: expected,
});

test('опорная дата из факта и прогноза склеивается в одну строку', () => {
  const { rows, forecastStart } = toChartData([
    fact('2026-10-23', 1), fact('2026-10-24', 2, 1),
    forecast('2026-10-24', 5, 3, 2.5), forecast('2026-10-25', 1, 0, 0.1),
  ]);
  assert.deepEqual(rows.map((r) => r.date), ['2026-10-23', '2026-10-24', '2026-10-25']);
  assert.equal(new Set(rows.map((r) => r.date)).size, rows.length, 'даты на оси не повторяются');
  assert.equal(forecastStart, '2026-10-24');
  assert.deepEqual(rows[1], { date: '2026-10-24', deleted: 2, estimated: 1, atRisk: 3, forecast: 2.5 });
});

test('факт и прогноз не смешиваются: в прошлом нет прогноза, в будущем нет удалений', () => {
  const { rows } = toChartData([fact('2026-10-23', 4, 2), forecast('2026-10-25', 2, 1, 0.7)]);
  assert.deepEqual(rows[0], { date: '2026-10-23', deleted: 4, estimated: 2, atRisk: null, forecast: null });
  assert.deepEqual(rows[1], { date: '2026-10-25', deleted: null, estimated: null, atRisk: 1, forecast: 0.7 });
});

test('оценочные удаления не попадают в фактические', () => {
  const { rows } = toChartData([fact('2026-09-29', 0, 1)]);
  assert.equal(rows[0].deleted, 0);
  assert.equal(rows[0].estimated, 1);
});

test('«под риском» — услуги с риском, а не все продления дня', () => {
  const { rows } = toChartData([forecast('2026-11-02', 4, 1, 0.3)]);
  assert.equal(rows[0].atRisk, 1);
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
