import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import MatricesInput, { errorText } from '../components/MatricesInput.jsx';

/**
 * Interfaz del experto: sin sesión, autenticada por el token del enlace (RF-15..RF-19).
 * Después del primer envío, el mismo enlace sirve para modificar el input durante el plazo de
 * edición (RF-52); cada modificación queda como una versión nueva.
 */

function post(path, body) {
  return fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function fmt(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function Shell({ children }) {
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">forgethreads <span>· invitación</span></div>
        </div>
      </header>
      <main className="wrap">{children}</main>
    </>
  );
}

export default function ExpertInvite() {
  const { token } = useParams();
  const [info, setInfo] = useState(null);
  const [error, setError] = useState('');
  const [texts, setTexts] = useState(null);
  const [validation, setValidation] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [done, setDone] = useState(null); // respuesta del envío
  const base = `/app/invite/${encodeURIComponent(token)}`;

  useEffect(() => {
    fetch(base)
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (res.ok) setInfo(body);
        else setError(errorText(body, 'No se pudo abrir la invitación.'));
      })
      .catch(() => setError('Error de red. Intenta nuevamente.'));
  }, [base]);

  const validate = useCallback((t) => post(`${base}/validate`, t), [base]);

  async function submit() {
    setBusy(true);
    setMsg('');
    try {
      const res = await post(`${base}/submit`, texts);
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        setDone(body);
        return;
      }
      setConfirming(false);
      if (body.detail && typeof body.detail === 'object' && 'valid' in body.detail) {
        setValidation(body.detail);
        setMsg('Las matrices tienen errores.');
      } else if (res.status === 410 || res.status === 409 || res.status === 404) {
        setError(errorText(body, 'La invitación ya no está disponible.'));
      } else {
        setMsg(errorText(body, 'No se pudo enviar. Intenta nuevamente.'));
      }
    } catch {
      setMsg('Error de red. Intenta nuevamente.');
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <Shell>
        <div className="card card-form">
          <h1>¡Gracias!</h1>
          <p className="sub">
            {done.version > 1 ? `Guardamos los cambios de tus matrices (versión ${done.version})` : 'Recibimos tus matrices'} para «{info.proyecto}».
            {done.editable_hasta
              ? ` Si necesitas corregir algo, vuelve a abrir este mismo enlace antes del ${fmt(done.editable_hasta)}.`
              : ' Ya puedes cerrar esta página.'}
          </p>
        </div>
      </Shell>
    );
  }
  if (error) {
    return (
      <Shell>
        <div className="card card-form">
          <h1>Invitación no disponible</h1>
          <p className="status err">{error}</p>
        </div>
      </Shell>
    );
  }
  if (!info) return <Shell><div className="card"><div className="empty">Cargando…</div></div></Shell>;

  const valid = validation?.valid === true && texts;
  const labels = { causes: info.causas, effects: info.efectos };
  const editing = info.modo === 'edicion';

  return (
    <Shell>
      <div className="card card-form">
        <h1 style={{ fontSize: 22 }}>{info.proyecto}</h1>
        {info.descripcion && <p className="sub" style={{ whiteSpace: 'pre-wrap' }}>{info.descripcion}</p>}
        {editing && (
          <div className="validation" style={{ marginBottom: 12 }}>
            <p className="small" style={{ margin: 0 }}>
              <strong>Ya enviaste tus matrices</strong> (versión {info.version}). Puedes modificarlas hasta el{' '}
              {fmt(info.editable_hasta)}: abajo están los valores que enviaste. Al confirmar se guarda una versión nueva.
            </p>
          </div>
        )}
        <p className="sub">
          Invitación para <strong>{info.nombre ? `${info.nombre} (${info.email})` : info.email}</strong>. Ingresa tus matrices CC (causas × causas), CE (causas × efectos)
          y EE (efectos × efectos) con valores en [0,1]. Puedes subir los CSV (separador <span className="mono">,</span> o{' '}
          <span className="mono">;</span>, decimal con punto o coma, etiquetas en la primera fila y columna; descarga las
          plantillas) o ingresarlas a mano. La diagonal de CC y EE vale 1. Después de enviar, con este mismo enlace puedes
          modificarlas durante {info.plazo_edicion_dias} días.
        </p>
        <div className="kv" style={{ marginBottom: 16 }}>
          <span>Causas ({info.causas.length})</span><span className="small">{info.causas.join(', ')}</span>
          <span>Efectos ({info.efectos.length})</span><span className="small">{info.efectos.join(', ')}</span>
        </div>
        <div className="grid">
          <MatricesInput
            validate={validate}
            labels={labels}
            validation={validation}
            onValidation={(v) => { setValidation(v); setConfirming(false); }}
            onTexts={setTexts}
            onMessage={(text) => setMsg(text || '')}
            initial={info.actual || undefined}
          />
          {!confirming && (
            <div className="actions">
              <button type="button" className="btn-primary" disabled={!valid} onClick={() => setConfirming(true)}>{editing ? 'Guardar cambios' : 'Enviar'}</button>
              <span className="status err" role="status" aria-live="polite">{msg}</span>
            </div>
          )}
          {confirming && (
            <div className="validation">
              <p className="small" style={{ marginTop: 0 }}>
                {editing
                  ? '¿Guardar estos cambios? Reemplazan a tu envío anterior en los cálculos nuevos.'
                  : `¿Confirmas el envío? Podrás modificarlo con este enlace durante ${info.plazo_edicion_dias} días.`}
              </p>
              <div className="actions">
                <button type="button" className="btn-primary" disabled={busy} onClick={submit}>{busy ? 'Enviando…' : editing ? 'Confirmar cambios' : 'Confirmar envío'}</button>
                <button type="button" className="btn" disabled={busy} onClick={() => setConfirming(false)}>Volver</button>
                <span className="status err" role="status" aria-live="polite">{msg}</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </Shell>
  );
}
