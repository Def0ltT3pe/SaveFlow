import { Link, useParams } from 'react-router-dom';
import { getCustomer, useLive } from '../api/client';
import Money from '../components/Money';
import RiskBadge from '../components/RiskBadge';
import type { ServiceStatus } from '../types';

const STATUS: Record<ServiceStatus, string> = {
  active: 'Активна',
  suspended: 'Выключена',
  prolong_wait: 'Ожидает оплаты',
  deleted: 'Удалена',
  blocked: 'Заблокирована',
};
const TERM: Record<string, string> = {
  hour: 'Почасово', week: 'Неделя', month: 'Месяц', quarter_year: 'Квартал', quater_year: 'Квартал', half_year: 'Полгода', year: 'Год',
};
const fmtDate = (iso?: string | null) => (iso ? new Date(iso).toLocaleString('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }) : '—');

export default function CustomerDetailPage() {
  const { id = '' } = useParams();
  const { data: c, error, loading } = useLive(() => getCustomer(id), [id]);

  if (loading && !c) return <p className="muted">Загружаем клиента…</p>;
  if (error && !c) return (
    <div className="page"><p className="error">Не удалось загрузить клиента: {error}</p><Link to="/customers">← К списку</Link></div>
  );
  if (!c) return null;

  const maxContribution = Math.max(1, ...c.factors.map((f) => Math.abs(f.contribution)));

  return (
    <div className="page">
      <Link to="/customers" className="back">← К списку клиентов</Link>
      <div className="page-head">
        <div>
          <h1>{c.email}</h1>
          <span className="muted">ID <span className="num">{c.id}</span></span>
        </div>
        <RiskBadge level={c.riskLevel} score={c.score} />
      </div>

      <div className="grid-4">
        <div className="panel stat"><span className="muted">Основной баланс</span><Money value={c.balance} className="stat-v" /></div>
        <div className="panel stat"><span className="muted">Бонусный баланс{c.bonusState === false ? ' (недоступен)' : ''}</span><Money value={c.bonusBalance} className="stat-v" /></div>
        <div className="panel stat"><span className="muted">Пополнено всего</span><Money value={c.totalReplenished} className="stat-v" /></div>
        <div className="panel stat loss-stat"><span className="muted">Ожидаемые потери</span><Money value={c.expectedLoss} className="stat-v" /><span className="muted">из <Money value={c.revenueAtRisk} /> под угрозой</span></div>
      </div>

      <section className="panel">
        <h2>Почему такая оценка</h2>
        {c.riskLevel === 'unknown' && <p className="muted">Данных по клиенту недостаточно — оценка не рассчитана.</p>}
        {c.explanation && <p>{c.explanation}</p>}
        {c.factors.length === 0 && c.riskLevel !== 'unknown' && <p className="muted">Признаков риска не найдено.</p>}
        <ul className="factors">
          {c.factors.map((f) => (
            <li key={f.key}>
              <div className="factor-top">
                <b>{f.label}</b>
                <span className="num factor-pts">{f.contribution > 0 ? '+' : ''}{f.contribution.toFixed(1)}</span>
              </div>
              <div className="bar"><div className={`bar-fill ${f.contribution < 0 ? 'neg' : ''}`} style={{ width: `${(Math.abs(f.contribution) / maxContribution) * 100}%` }} /></div>
              <p className="muted">{f.description}</p>
            </li>
          ))}
        </ul>
      </section>

      <section className="panel table-wrap">
        <h2>Услуги</h2>
        <table>
          <thead>
            <tr><th>Название</th><th>Статус</th><th className="r">Цена</th><th>Период</th><th>Автопродление</th><th>Истекает</th></tr>
          </thead>
          <tbody>
            {c.services.length === 0 && <tr><td colSpan={6} className="muted">У клиента нет услуг.</td></tr>}
            {c.services.map((s) => (
              <tr key={s.id}>
                <td>{s.name}</td>
                <td><span className={`status status-${s.status}`}>{STATUS[s.status] ?? s.status}</span></td>
                <td className="r"><Money value={s.price} /></td>
                <td>{TERM[s.paymentTerm] ?? s.paymentTerm}</td>
                <td>{s.autoprolong ? 'Включено' : <b className="warn">Выключено</b>}</td>
                <td className="num">{fmtDate(s.expiresAt)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
