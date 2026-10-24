import { useEffect, useState, type FormEvent } from 'react';
import { OIDC_LOGIN_URL, errorMessage, getOidcConfig, login } from '../api/client';

const LOGIN_ERRORS: Record<string, string> = { 'Invalid credentials': 'Неверный логин или пароль' };

export default function LoginPage({ onLogin, ssoError }: { onLogin: () => void; ssoError?: string | null }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(ssoError ?? null);
  const [sso, setSso] = useState<string | null>(null); // название провайдера, если вход через OIDC включён

  useEffect(() => {
    getOidcConfig()
      .then((c) => setSso(c.enabled ? c.name ?? 'SSO' : null))
      .catch(() => setSso(null)); // без OIDC остаётся вход по логину и паролю
  }, []);

  useEffect(() => {
    if (ssoError) setError(ssoError);
  }, [ssoError]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await login({ username, password });
      onLogin();
    } catch (err) {
      const message = errorMessage(err);
      setError(LOGIN_ERRORS[message] ?? message);
    }
  };

  return (
    <div className="login-wrap">
      <form className="panel login" onSubmit={submit}>
        <h1>Вход</h1>
        {sso && (
          <>
            {/* после отказа провайдер помнит прежнего пользователя: просим его снова спросить пароль */}
            <a className="btn btn-sso" href={ssoError ? `${OIDC_LOGIN_URL}?prompt=login` : OIDC_LOGIN_URL}>
              {ssoError ? `Войти через ${sso} другим пользователем` : `Войти через ${sso}`}
            </a>
            <div className="login-or" role="separator">или по логину и паролю</div>
          </>
        )}
        <label>Логин<input type="text" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus /></label>
        <label>Пароль<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></label>
        {error && <p className="error">{error}</p>}
        <button className="btn" type="submit">Войти</button>
      </form>
    </div>
  );
}
