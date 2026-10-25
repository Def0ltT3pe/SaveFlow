/**
 * Насколько серьёзны ожидаемые потери. Сама сумма ничего не говорит о масштабе (100 € — много или мало?),
 * поэтому цвет зависит от доли потерь в выручке за горизонт прогноза, а не от суммы.
 */
export type LossLevel = 'none' | 'low' | 'medium' | 'high';

/** Пороги доли потерь: до 10 % — низкая, до 25 % — заметная, от 25 % — высокая. */
export const LOSS_SHARE_MEDIUM = 0.1;
export const LOSS_SHARE_HIGH = 0.25;

export function lossSeverity(expectedLoss: number, horizonRevenue: number): { share: number | null; level: LossLevel } {
  if (!(horizonRevenue > 0)) return { share: null, level: 'none' };
  const share = Math.max(expectedLoss, 0) / horizonRevenue;
  const level: LossLevel = share >= LOSS_SHARE_HIGH ? 'high' : share >= LOSS_SHARE_MEDIUM ? 'medium' : 'low';
  return { share, level };
}

export const LOSS_LEVEL_LABEL: Record<LossLevel, string> = {
  none: 'нет выручки в горизонте',
  low: 'низкая доля',
  medium: 'заметная доля',
  high: 'высокая доля',
};

/** «12 %» — с одним знаком после запятой для долей меньше 10 %. */
export const formatShare = (share: number) =>
  `${(share * 100).toLocaleString('ru-RU', { maximumFractionDigits: share < 0.1 ? 1 : 0 })} %`;
