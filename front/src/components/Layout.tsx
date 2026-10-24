import { NavLink, Outlet } from 'react-router-dom';
import { useServerEvents } from '../api/client';
import type { User } from '../types';

const NAV = [
  { to: '/', label: 'Обзор', end: true },
  { to: '/customers', label: 'Клиенты' },
  { to: '/settings', label: 'Модель и данные' },
];

export default function Layout({ user, onLogout }: { user: User; onLogout: () => void }) {
  useServerEvents();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          <span>SaveFlow</span>
        </div>
        <nav>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? 'nav active' : 'nav')}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <a href="/api/v1/docs" target="_blank" rel="noreferrer">API-документация</a>
          <a href="/metrics" target="_blank" rel="noreferrer">Метрики</a>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <span className="live" title="Данные обновляются автоматически">
            <span className="live-dot" /> Обновляется автоматически
          </span>
          <div className="topbar-user">
            <span>{user.username}</span>
            <button className="btn btn-ghost" onClick={onLogout}>Выйти</button>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
