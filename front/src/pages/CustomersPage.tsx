import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { getCustomers, useLive } from '../api/client';
import Money from '../components/Money';
import RiskBadge, { riskLabel } from '../components/RiskBadge';
import type { CustomerSortField, RiskLevel, SortOrder } from '../types';

const COLUMNS: { field: CustomerSortField; label: string; numeric?: boolean }[] = [
  { field: 'id', label: 'ID' },
  { field: 'email', label: 'Email' },
  { field: 'balance', label: 'Баланс', numeric: true },
  { field: 'totalReplenished', label: 'Пополнено всего', numeric: true },
  { field: 'score', label: 'Риск (isk-score)' },
];

export default function CustomersPage() {
  const [params, setParams] = useSearchParams();
  const level = (params.get('risk') as RiskLevel | null) ?? undefined;
  const [sortBy, setSortBy] = useState<CustomerSortField>('score');
  const [order, setOrder] = useState<SortOrder>('desc');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');

  // запрос уходит, когда пользователь перестал печатать, а не на каждую букву
  useEffect(() => {
    const t = window.setTimeout(() => setQuery(search.trim()), 300);
    return () => window.clearTimeout(t);
  }, [search]);

  const { data, error, loading } = useLive(
    () => getCustomers({ sortBy, order, riskLevel: level, search: query || undefined }),
    [sortBy, order, level, query],
  );

  const toggleSort = (f: CustomerSortField) => {
    if (f === sortBy) setOrder(order === 'asc' ? 'desc' : 'asc');
    else {
      setSortBy(f);
      setOrder(f === 'id' || f === 'email' ? 'asc' : 'desc');
    }
  };

  const setLevel = (l?: RiskLevel) => {
    const next = new URLSearchParams(params);
    if (l) next.set('risk', l);
    else next.delete('risk');
    setParams(next);
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1>Клиенты под риском</h1>
        <span className="muted num">{data ? `${data.length} в списке` : ''}</span>
      </div>

      <div className="filters">
        <input type="search" placeholder="Поиск по email или ID" value={search} onChange={(e) => setSearch(e.target.value)} />
        <div className="chips" role="group" aria-label="Уровень риска">
          <button className={`chip ${!level ? 'on' : ''}`} onClick={() => setLevel()}>Все</button>
          {(['high', 'medium', 'low', 'unknown'] as RiskLevel[]).map((l) => (
            <button key={l} className={`chip ${level === l ? 'on' : ''}`} onClick={() => setLevel(l)}>{riskLabel(l)}</button>
          ))}
        </div>
      </div>

      {error && <p className="error">Не удалось загрузить клиентов: {error}</p>}

      <div className="panel table-wrap">
        <table>
          <thead>
            <tr>
              {COLUMNS.map((c) => {
                const active = c.field === sortBy;
                return (
                  <th key={c.field} className={c.numeric ? 'r' : ''} aria-sort={active ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
                    <button className="th-btn" onClick={() => toggleSort(c.field)}>
                      {c.label}
                      <span className={`arrow ${active ? 'on' : ''}`}>{active ? (order === 'asc' ? '▲' : '▼') : '↕'}</span>
                    </button>
                  </th>
                );
              })}
              <th>Причина</th>
            </tr>
          </thead>
          <tbody>
            {loading && !data && (
              <tr><td colSpan={6} className="muted">Загружаем…</td></tr>
            )}
            {data?.length === 0 && (
              <tr><td colSpan={6} className="muted">Никого не нашли. Сбросьте фильтры или загрузите данные в разделе «Модель и данные».</td></tr>
            )}
            {data?.map((c) => (
              <tr key={c.id}>
                <td className="num"><Link to={`/customers/${c.id}`}>{c.id}</Link></td>
                <td><Link to={`/customers/${c.id}`}>{c.email}</Link></td>
                <td className="r"><Money value={c.balance} /></td>
                <td className="r"><Money value={c.totalReplenished} /></td>
                <td><RiskBadge level={c.riskLevel} score={c.score} /></td>
                <td className="reason">{c.mainReason ?? (c.riskLevel === 'unknown' ? 'Недостаточно данных' : '—')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
