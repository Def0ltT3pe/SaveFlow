import { useEffect, useRef } from 'react';
import {
  Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
  type TooltipProps,
} from 'recharts';
import { type ChartRow, chartMinWidth, forecastScrollLeft, fullDay, shortDay } from '../api/chart';
import { formatMoney } from './Money';

const euros = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });

const COLORS = { lost: '#c8372d', risk: '#14444a', money: '#E97C35' };

// Линии рисуются в евро, а не в евроцентах: так деления правой оси круглые (5 €, 10 €…)
const inEuros = (key: 'lostRevenue' | 'expectedLoss') => (r: ChartRow) => (r[key] === null ? null : r[key] / 100);

/** Подсказка по дню: клиенты и деньги, ниже — услуги, из которых они сложились. */
function DayTooltip({ active, payload }: TooltipProps<number, string>) {
  const row = active ? (payload?.[0]?.payload as ChartRow | undefined) : undefined;
  if (!row) return null;
  return (
    <div className="chart-tip">
      <b>{fullDay(row.date)}</b>
      {row.lostClients !== null && (
        <>
          <div className="chart-tip-head">Факт</div>
          <div>Ушли клиенты: <b className="num">{row.lostClients}</b></div>
          <div>Недополучено: <b className="num">{formatMoney(row.lostRevenue)}</b></div>
          <div className="muted">
            Удалено услуг: {row.deleted}{row.estimated ? ` + ${row.estimated} с оценённой датой` : ''}
          </div>
        </>
      )}
      {row.riskClients !== null && (
        <>
          <div className="chart-tip-head">Прогноз</div>
          <div>Клиентов под риском: <b className="num">{row.riskClients}</b></div>
          <div>Уйдут по оценке модели: <b className="num">≈ {decimal.format(row.expectedClients ?? 0)}</b></div>
          <div>Ожидаемые потери: <b className="num">{formatMoney(row.expectedLoss)}</b></div>
          <div>Без денег на продление: <span className="num">{formatMoney(row.revenueAtRisk)}</span></div>
          <div className="muted">Продлений услуг: {row.dueServices}, под риском: {row.atRiskServices}</div>
        </>
      )}
    </div>
  );
}

/**
 * График оттока: столбики — клиенты, линии — деньги (правая ось). На узком экране он шире контейнера
 * и прокручивается вбок, а не сжимает столбики: при открытии граница «Прогноз →» встаёт в середину.
 */
export default function ChurnChart({ rows, forecastStart }: { rows: ChartRow[]; forecastStart: string | null }) {
  const scroller = useRef<HTMLDivElement>(null);
  const minWidth = chartMinWidth(rows.length);

  useEffect(() => {
    const el = scroller.current;
    if (!el || el.scrollWidth <= el.clientWidth) return;
    const index = forecastStart ? rows.findIndex((r) => r.date === forecastStart) : -1;
    el.scrollLeft = forecastScrollLeft(index, rows.length, el.scrollWidth, el.clientWidth);
  }, [rows.length, forecastStart]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="chart" ref={scroller}>
      <div style={{ minWidth }}>
        <ResponsiveContainer width="100%" height={320}>
          <ComposedChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap="12%">
            <CartesianGrid strokeDasharray="3 3" stroke="#dde3e1" />
            <XAxis dataKey="date" tickFormatter={shortDay} minTickGap={16} />
            <YAxis yAxisId="clients" allowDecimals={false} width={36} />
            <YAxis yAxisId="money" orientation="right" width={64} tickFormatter={(v: number) => euros.format(v)} />
            <Tooltip content={<DayTooltip />} />
            <Legend />
            {forecastStart && <ReferenceLine yAxisId="clients" x={forecastStart} stroke="#6b7a77" strokeDasharray="4 4" label={{ value: 'Прогноз →', position: 'insideTopRight', fill: '#6b7a77' }} />}
            {/* одна стопка на день: в прошлом — ушедшие клиенты, в будущем — клиенты под риском; так столбик вдвое шире */}
            <Bar yAxisId="clients" dataKey="lostClients" stackId="day" name="Ушли клиенты (факт)" fill={COLORS.lost} />
            <Bar yAxisId="clients" dataKey="riskClients" stackId="day" name="Клиенты под риском (прогноз)" fill={COLORS.risk} />
            <Line yAxisId="money" type="linear" dataKey={inEuros('lostRevenue')} name="Недополучено, €" stroke={COLORS.money} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
            <Line yAxisId="money" type="linear" dataKey={inEuros('expectedLoss')} name="Ожидаемые потери, €" stroke={COLORS.money} strokeWidth={2} strokeDasharray="6 4" dot={false} activeDot={{ r: 4 }} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
