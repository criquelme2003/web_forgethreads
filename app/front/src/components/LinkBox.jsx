import { useState } from 'react';

/**
 * Enlace personal recién generado (invitación o activación). Solo se muestra en ese momento:
 * en la base se guarda únicamente su hash.
 */
export default function LinkBox({ title, link, envio, envioError, email, onClose, hint }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  }
  let delivery;
  if (envio === 'manual') delivery = 'Copia el enlace y envíalo.';
  else if (envioError) delivery = `No se pudo enviar el correo (${envioError}). Envía el enlace a mano.`;
  else delivery = `Enviado por correo a ${email}. También puedes copiar el enlace.`;
  return (
    <div className="validation" style={{ marginBottom: 12 }}>
      <div className="small" style={{ marginBottom: 6 }}>
        <strong>{title}</strong>{' '}
        <span className={envioError ? 'status err' : 'muted'}>{delivery}</span>
      </div>
      <div className="controls" style={{ marginBottom: 0 }}>
        <input className="mono link-input" readOnly value={link} onFocus={(e) => e.target.select()} aria-label="Enlace" />
        <button type="button" onClick={copy}>{copied ? 'Copiado' : 'Copiar'}</button>
        {onClose && <button type="button" onClick={onClose}>Ocultar</button>}
      </div>
      {hint && <div className="small muted" style={{ marginTop: 6 }}>{hint}</div>}
    </div>
  );
}
