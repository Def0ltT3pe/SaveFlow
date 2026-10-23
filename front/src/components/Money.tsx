import type { Cents } from '../types';

const fmt = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'EUR', minimumFractionDigits: 2 });

/** Данные приходят в еврокопейках: 100 = 1 €. */
export const formatMoney = (cents: Cents | null | undefined) =>
  cents == null || Number.isNaN(cents) ? '—' : fmt.format(cents / 100);

export default function Money({ value, className }: { value: Cents | null | undefined; className?: string }) {
  return <span className={`num ${className ?? ''}`}>{formatMoney(value)}</span>;
}
