import type { RiskLevel } from '../types';

const LABELS: Record<RiskLevel, string> = {
  high: 'Высокий риск',
  medium: 'Средний риск',
  low: 'Низкий риск',
  unknown: 'Нет оценки',
};

export const riskLabel = (l: RiskLevel) => LABELS[l];

interface Props {
  level: RiskLevel;
  score?: number | null;
}

export default function RiskBadge({ level, score }: Props) {
  return (
    <span className={`badge badge-${level}`} title={level === 'unknown' ? 'Данных по клиенту недостаточно для оценки' : undefined}>
      <span className="dot" />
      {LABELS[level]}
      {level !== 'unknown' && score != null && <span className="num badge-score">{Math.round(score)}</span>}
    </span>
  );
}
