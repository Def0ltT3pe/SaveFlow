import { Link } from 'react-router-dom';
import {
  Area, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { getSummary, useLive } from '../api/client';
import Money from '../components/Money';
import { riskLabel } from '../components/RiskBadge';
import type { ChurnPoint, RiskLevel } from '../types';

const LEVELS: RiskLevel[] = ['high', 'medium', 'low', 'unknown'];
const shortDate = (iso: string) => new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: 'short' });

function toChartData(series: ChurnPoint[]) {
  return series.map((p) => ({
    date: p.date,
    atRisk: p.atRiskServices,
    deleted: p.isForecast ? null : p.deletedServices ?? null, // факт
    forecast: p.isForecast ? p.forecastNonRenewals ?? null : null, // прогноз
  }));
}

export default function DashboardPage() {
  const { data, error, loading, updatedAt } = useLive(getSummary);

  if (loading && !data) return <p className="muted">Загружаем сводку…</p>;
  if (error && !data) return <p className="error">Не удалось загрузить сводку: {error}</p>;
  if (!data) return null;

  const total = LEVELS.reduce((s, l) => s + (data.byLevel[l] ?? 0), 0) || 1;
  const chart = data.churnSeries?.length ? toChartData(data.churnSeries) : [];
  const firstForecast = data.churnSeries?.find((p) => p.isForecast)?.date;

  return (
    <div className="page">
      <div className="page-head">
        <h1>Обзор оттока</h1>
        {updatedAt && <span className="muted">Обновлено в {updatedAt.toLocaleTimeString('ru-RU')}</span>}
      </div>
      {error && <p className="error">Данные могут быть устаревшими: {error}</p>}

      <section className="loss panel">
        <div>
          <div className="muted">Ожидаемые потери</div>
          <Money value={data.expectedLoss} className="loss-value" />
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
          <div className="chart">
            <ResponsiveContainer width="100%" height={300}>
              <ComposedChart data={chart} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#dde3e1" />
                <XAxis dataKey="date" tickFormatter={shortDate} />
                <YAxis allowDecimals={false} />
                <Tooltip labelFormatter={(v) => new Date(String(v)).toLocaleDateString('ru-RU')} />
                <Legend />
                {firstForecast && <ReferenceLine x={firstForecast} stroke="#6b7a77" strokeDasharray="4 4" label={{ value: 'Прогноз →', position: 'insideTopRight', fill: '#6b7a77' }} />}
                <Area type="monotone" dataKey="deleted" name="Удалено (факт)" fill="#c8372d" fillOpacity={0.25} stroke="#c8372d" />
                <Line type="monotone" dataKey="atRisk" name="Услуг под риском" stroke="#14444a" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="forecast" name="Не продлятся (прогноз)" stroke="#c8372d" strokeWidth={2} strokeDasharray="6 4" dot={{ r: 3 }} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>
    </div>
  );
}
