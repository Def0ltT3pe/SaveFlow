import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { emitChanged, errorMessage, getSettings, patchSettings, putSnapshot } from '../api/client';
import type { ModelSettings, ModelSettingsPatch, SourceSnapshot } from '../types';

type Save = 'idle' | 'saving' | 'saved' | 'error';

export default function SettingsPage() {
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [save, setSave] = useState<Save>('idle');
  const [saveError, setSaveError] = useState<string | null>(null);
  const [snap, setSnap] = useState<{ kind: 'ok' | 'err' | 'busy'; text: string } | null>(null);
  const timer = useRef<number>();
  const pending = useRef<ModelSettingsPatch>({});

  useEffect(() => {
    getSettings().then(setSettings).catch((e) => setLoadError(errorMessage(e)));
    return () => window.clearTimeout(timer.current);
  }, []);

  // Слайдер двигается сразу, на бэкенд изменения уходят пачкой через 400 мс — модель пересчитывается без reload.
  const schedule = (patch: ModelSettingsPatch) => {
    pending.current = {
      ...pending.current,
      ...patch,
      coefficients: { ...pending.current.coefficients, ...patch.coefficients },
      thresholds: { ...pending.current.thresholds, ...patch.thresholds },
    };
    setSave('saving');
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      const body = pending.current;
      pending.current = {};
      try {
        const fresh = await patchSettings(body);
        setSettings(fresh);
        setSave('saved');
        setSaveError(null);
        emitChanged(); // обновить все открытые страницы
      } catch (e) {
        setSave('error');
        setSaveError(errorMessage(e));
      }
    }, 400);
  };

  const setCoef = (key: string, value: number) => {
    setSettings((s) => s && { ...s, coefficients: s.coefficients.map((c) => (c.key === key ? { ...c, value } : c)) });
    schedule({ coefficients: { [key]: value } });
  };

  const setThreshold = (key: 'medium' | 'high', value: number) => {
    setSettings((s) => s && { ...s, thresholds: { ...s.thresholds, [key]: value } });
    schedule({ thresholds: { [key]: value } });
  };

  const onFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setSnap({ kind: 'busy', text: 'Загружаем…' });
    try {
      const json = JSON.parse(await file.text()) as SourceSnapshot;
      if (!json.accounts || !json.services || !json.transactions) {
        throw new Error('В файле должны быть массивы accounts, services и transactions');
      }
      await putSnapshot(json);
      setSnap({
        kind: 'ok',
        text: `Снимок заменён: аккаунтов ${json.accounts.length}, услуг ${json.services.length}, транзакций ${json.transactions.length}. Оценки пересчитаны.`,
      });
      emitChanged();
    } catch (err) {
      setSnap({ kind: 'err', text: err instanceof SyntaxError ? 'Файл не похож на JSON' : errorMessage(err) });
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1>Модель и данные</h1>
        <span className={`save-state save-${save}`} aria-live="polite">
          {save === 'saving' && 'Сохраняем и пересчитываем…'}
          {save === 'saved' && 'Сохранено, оценки пересчитаны'}
          {save === 'error' && `Не сохранилось: ${saveError}`}
        </span>
      </div>

      <section className="panel">
        <h2>Коэффициенты isk-score</h2>
        <p className="muted">Чем больше коэффициент, тем сильнее признак влияет на оценку. Изменения применяются сразу и сохраняются после перезапуска.</p>
        {loadError && <p className="error">Не удалось загрузить настройки: {loadError}</p>}
        {!settings && !loadError && <p className="muted">Загружаем…</p>}
        <div className="sliders">
          {settings?.coefficients.map((c) => (
            <div key={c.key} className="slider">
              <label htmlFor={`c-${c.key}`}>
                <b>{c.label}</b>
                {c.description && <span className="muted"> {c.description}</span>}
              </label>
              <div className="slider-row">
                <input id={`c-${c.key}`} type="range" min={c.min} max={c.max} step={c.step ?? 0.05} value={c.value} onChange={(e) => setCoef(c.key, Number(e.target.value))} />
                <input className="num slider-num" type="number" min={c.min} max={c.max} step={c.step ?? 0.05} value={c.value} onChange={(e) => !Number.isNaN(e.target.valueAsNumber) && setCoef(c.key, e.target.valueAsNumber)} aria-label={`${c.label}: значение`} />
              </div>
            </div>
          ))}
        </div>
      </section>

      {settings && (
        <section className="panel">
          <h2>Пороги уровней риска</h2>
          <p className="muted">Score от «среднего» порога — средний риск, от «высокого» — высокий. Ниже — низкий.</p>
          <div className="sliders">
            {(['medium', 'high'] as const).map((k) => (
              <div key={k} className="slider">
                <label htmlFor={`t-${k}`}><b>{k === 'medium' ? 'Средний риск от' : 'Высокий риск от'}</b></label>
                <div className="slider-row">
                  <input id={`t-${k}`} type="range" min={0} max={100} step={1} value={settings.thresholds[k]} onChange={(e) => setThreshold(k, Number(e.target.value))} />
                  <input className="num slider-num" type="number" min={0} max={100} value={settings.thresholds[k]} onChange={(e) => !Number.isNaN(e.target.valueAsNumber) && setThreshold(k, e.target.valueAsNumber)} aria-label={k === 'medium' ? 'Порог среднего риска' : 'Порог высокого риска'} />
                </div>
              </div>
            ))}
          </div>
          {settings.thresholds.medium >= settings.thresholds.high && <p className="error">Порог среднего риска должен быть ниже порога высокого.</p>}
        </section>
      )}

      <section className="panel">
        <h2>Исходные данные</h2>
        <p className="muted">Загрузите JSON с массивами accounts, services и transactions — текущий снимок будет полностью заменён.</p>
        <label className="btn file-btn">
          Выбрать файл снимка
          <input type="file" accept="application/json,.json" onChange={onFile} hidden />
        </label>
        {snap && <p className={snap.kind === 'err' ? 'error' : snap.kind === 'ok' ? 'ok' : 'muted'}>{snap.text}</p>}
      </section>
    </div>
  );
}
