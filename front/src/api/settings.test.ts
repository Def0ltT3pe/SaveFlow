import assert from 'node:assert/strict';
import { test } from 'node:test';
import { isAtDefaults, resetToDefaultsPatch, toSettings, type BSettings } from './backend.ts';

const backend: BSettings = {
  settings: { weights: { prolong_wait: 70, suspended: 10 }, thresholds: { high: 80, medium: 30 }, horizonDays: 30 },
  version: 7,
  features: [
    { key: 'prolong_wait', title: 'Ожидает оплаты', description: '', defaultWeight: 45 },
    { key: 'suspended', title: 'Сервер выключен', description: '', defaultWeight: 10 },
  ],
  defaults: { weights: { prolong_wait: 45, suspended: 10 }, thresholds: { high: 60, medium: 30 } },
};

test('значения по умолчанию приходят с бэкенда', () => {
  const s = toSettings(backend);
  assert.deepEqual(s.coefficients.map((c) => [c.key, c.value, c.defaultValue]), [['prolong_wait', 70, 45], ['suspended', 10, 10]]);
  assert.deepEqual(s.defaultThresholds, { high: 60, medium: 30 });
});

test('сброс возвращает все коэффициенты и оба порога', () => {
  assert.deepEqual(resetToDefaultsPatch(toSettings(backend)), {
    coefficients: { prolong_wait: 45, suspended: 10 },
    thresholds: { high: 60, medium: 30 },
  });
});

test('кнопка сброса неактивна, когда всё и так по умолчанию', () => {
  assert.equal(isAtDefaults(toSettings(backend)), false);
  const reset = toSettings({ ...backend, settings: { ...backend.settings, weights: { prolong_wait: 45 }, thresholds: { high: 60, medium: 30 } } });
  assert.equal(isAtDefaults(reset), true);
  const onlyThreshold = toSettings({ ...backend, settings: { ...backend.settings, weights: { prolong_wait: 45 } } });
  assert.equal(isAtDefaults(onlyThreshold), false);
});
