import { useEffect, useRef } from 'react';
import {
  Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { type ChartRow, chartMinWidth, forecastScrollLeft, fullDay, shortDay } from '../api/chart';

/**
 * График оттока. На узком экране он шире контейнера и прокручивается вбок, а не сжимает столбики:
 * при открытии прокрутка встаёт так, чтобы граница «Прогноз →» была в середине.
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
        <ResponsiveContainer width="100%" height={300}>
          <ComposedChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }} barCategoryGap="12%">
            <CartesianGrid strokeDasharray="3 3" stroke="#dde3e1" />
            <XAxis dataKey="date" tickFormatter={shortDay} minTickGap={16} />
            <YAxis allowDecimals={false} />
            <Tooltip labelFormatter={(v) => fullDay(String(v))} />
            <Legend />
            {forecastStart && <ReferenceLine x={forecastStart} stroke="#6b7a77" strokeDasharray="4 4" label={{ value: 'Прогноз →', position: 'insideTopRight', fill: '#6b7a77' }} />}
            {/* одна стопка на день: в прошлом — удаления, в будущем — продления под риском; так столбик вдвое шире */}
            <Bar dataKey="deleted" stackId="day" name="Удалено (факт)" fill="#c8372d" />
            <Bar dataKey="estimated" stackId="day" name="Удалено (дата оценена)" fill="#e9a29c" />
            <Bar dataKey="atRisk" stackId="day" name="Продлений под риском (прогноз)" fill="#14444a" />
            <Line type="linear" dataKey="forecast" name="Не продлятся (прогноз)" stroke="#c8372d" strokeWidth={2} strokeDasharray="6 4" dot={false} activeDot={{ r: 4 }} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
