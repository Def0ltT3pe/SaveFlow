// ЗАГЛУШКА: страницу входа делает другой участник. Контракт с App.tsx:
//   <LoginPage onLogin={() => void} />  — вызвать после успешного POST /auth/login (api.login).
import { useState, type FormEvent } from 'react';
import { errorMessage, login } from '../api/client';

export default function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await login({ username, password });
      onLogin();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <div className="login-wrap">
      <form className="panel login" onSubmit={submit}>
        <h1>Вход</h1>
        <label>Логин<input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus /></label>
        <label>Пароль<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        {error && <p className="error">{error}</p>}
        <button className="btn" type="submit">Войти</button>
      </form>
    </div>
  );
}
