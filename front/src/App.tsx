import { useCallback, useEffect, useState } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { getMe, logout, takeSsoResult } from './api/client';
import Layout from './components/Layout';
import CustomerDetailPage from './pages/CustomerDetailPage';
import CustomersPage from './pages/CustomersPage';
import DashboardPage from './pages/DashboardPage';
import LoginPage from './pages/LoginPage';
import SettingsPage from './pages/SettingsPage';
import type { User } from './types';

export default function App() {
  // undefined — ещё проверяем сессию, null — не авторизован
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [ssoError, setSsoError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    getMe().then(setUser).catch(() => setUser(null));
  }, []);

  useEffect(() => {
    // до проверки сессии: после входа через OIDC токен приходит в адресе страницы
    const sso = takeSsoResult();
    if (sso.error) setSsoError(sso.error);
    refresh();
    const onUnauthorized = () => setUser(null); // любой 401 из API выбрасывает на логин
    window.addEventListener('auth:unauthorized', onUnauthorized);
    return () => window.removeEventListener('auth:unauthorized', onUnauthorized);
  }, [refresh]);

  const onLogout = async () => {
    await logout().catch(() => undefined);
    setUser(null);
  };

  if (user === undefined) return <p className="muted splash">Проверяем сессию…</p>;

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage onLogin={refresh} ssoError={ssoError} />} />
        {/* AuthGuard: всё остальное — только для авторизованных */}
        <Route element={user ? <Layout user={user} onLogout={onLogout} /> : <Navigate to="/login" replace />}>
          <Route index element={<DashboardPage />} />
          <Route path="customers" element={<CustomersPage />} />
          <Route path="customers/:id" element={<CustomerDetailPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
