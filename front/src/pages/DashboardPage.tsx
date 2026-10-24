import { Link } from 'react-router-dom';
import { toChartData } from '../api/chart';
import { getSummary, useLive } from '../api/client';
import { LOSS_LEVEL_LABEL, formatShare, lossSeverity } from '../api/money';
import ChurnChart from '../components/ChurnChart';
import Money from '../components/Money';
import { riskLabel } from '../components/RiskBadge';
import type { RiskLevel } from '../types';

const LEVELS: RiskLevel[] = ['high', 'medium', 'low', 'unknown'];

export default function DashboardPage() {
  const { data, error, loading, updatedAt } = useLive(getSummary);

  if (loading && !data) return <p className="muted">Загружаем сводку…</p>;
  if (error && !data) return <p className="error">Не удалось загрузить сводку: {error}</p>;
  if (!data) return null;

  const total = LEVELS.reduce((s, l) => s + (data.byLevel[l] ?? 0), 0) || 1;
  const { rows: chart, forecastStart } = toChartData(data.churnSeries ?? []);
  const loss = lossSeverity(data.expectedLoss, data.horizonRevenue);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Обзор оттока</h1>
        {updatedAt && <span className="muted">Обновлено в {updatedAt.toLocaleTimeString('ru-RU')}</span>}
      </div>
      {error && <p className="error">Данные могут быть устаревшими: {error}</p>}

      <section className={`loss loss-${loss.level} panel`}>
        <div>
          <div className="muted">Ожидаемые потери</div>
          <Money value={data.expectedLoss} className="loss-value" />
          {loss.share !== null && (
            <span className={`loss-share loss-share-${loss.level}`}
                  title={`Доля ожидаемых потерь во всей выручке от продлений за ${data.horizonDays} дн.: до 10 % — низкая, до 25 % — заметная, от 25 % — высокая`}>
              {formatShare(loss.share)} выручки за {data.horizonDays} дн. · {LOSS_LEVEL_LABEL[loss.level]}
            </span>
          )}
          <div className="muted">
            из <Money value={data.revenueAtRisk} /> выручки под угрозой · услуг под риском: <b className="num">{data.servicesAtRisk}</b>
          </div>
        </div>
        <p className="loss-note">
          {data.lossExplanation ??
            'Ожидаемые потери — это стоимость услуг, которые могут не продлиться, умноженная на вероятность оттока клиента. Деньги на балансе, которых хватит на продление, снижают оценку.'}
        </p>
      </section>

      <section className="panel">
        <h2>Клиенты по уровням риска</h2>
        <div className="distribution" role="img" aria-label="Распределение клиентов по уровням риска">
          {LEVELS.map((l) => {
            const n = data.byLevel[l] ?? 0;
            return n > 0 ? <div key={l} className={`seg seg-${l}`} style={{ flexGrow: n }} title={`${riskLabel(l)}: ${n}`} /> : null;
          })}
        </div>
        <div className="level-cards">
          {LEVELS.map((l) => (
            <Link key={l} to={`/customers?risk=${l}`} className={`level-card level-${l}`}>
              <span className="level-n num">{data.byLevel[l] ?? 0}</span>
              <span>{riskLabel(l)}</span>
              <span className="muted num">{Math.round(((data.byLevel[l] ?? 0) / total) * 100)}%</span>
            </Link>
          ))}
        </div>
        <p className="muted">Всего клиентов: <b className="num">{data.customersTotal}</b>. Клиенты без оценки — те, по кому не хватает данных.</p>
      </section>

      <section className="panel">
        <h2>Отток услуг: факт и прогноз</h2>
        {chart.length === 0 ? (
          <p className="muted">История изменений статусов пока не поступала — график появится, когда бэкенд начнёт её отдавать.</p>
        ) : (
          <ChurnChart rows={chart} forecastStart={forecastStart} />
        )}
      </section>
    </div>
  );
}
