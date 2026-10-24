import assert from 'node:assert/strict';
import { test } from 'node:test';
import { formatShare, lossSeverity } from './money.ts';

test('цвет потерь зависит от доли в выручке, а не от суммы', () => {
  assert.equal(lossSeverity(100, 10_000).level, 'low'); // 1 %
  assert.equal(lossSeverity(100_000, 10_000_000).level, 'low'); // та же доля при сумме в 1000 раз больше
  assert.equal(lossSeverity(999, 10_000).level, 'low');
  assert.equal(lossSeverity(1_000, 10_000).level, 'medium'); // ровно 10 %
  assert.equal(lossSeverity(2_499, 10_000).level, 'medium');
  assert.equal(lossSeverity(2_500, 10_000).level, 'high'); // ровно 25 %
});

test('без выручки в горизонте доли нет', () => {
  assert.deepEqual(lossSeverity(0, 0), { share: null, level: 'none' });
  assert.deepEqual(lossSeverity(500, 0), { share: null, level: 'none' });
});

test('доля считается от выручки за горизонт', () => {
  assert.equal(lossSeverity(54_466, 215_584).share, 54_466 / 215_584);
  assert.equal(lossSeverity(-5, 100).share, 0);
});

test('доля печатается в процентах', () => {
  assert.equal(formatShare(0.2526), '25 %');
  assert.equal(formatShare(0.0123), '1,2 %');
});
