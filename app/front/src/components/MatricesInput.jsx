import { useEffect, useState } from 'react';
import ManualMatrices from './ManualMatrices.jsx';

const MATRICES = [
  { key: 'cc', name: 'CC', shape: 'm × m', hint: 'Causas × causas', rows: 'causes', cols: 'causes' },
  { key: 'ce', name: 'CE', shape: 'm × n', hint: 'Causas × efectos', rows: 'causes', cols: 'effects' },
  { key: 'ee', name: 'EE', shape: 'n × n', hint: 'Efectos × efectos', rows: 'effects', cols: 'effects' },
];
const PREVIEW_MAX = 40; // filas/columnas visibles por matriz

export function errorText(body, fallback) {
  if (!body || !body.detail) return fallback;
  if (typeof body.detail === 'string') return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join(' · ');
  return fallback;
}

function csvField(value) {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/** Plantilla CSV con las etiquetas del proyecto y la diagonal en 1 (CC y EE). */
function templateCsv(m, labels) {
  const rows = labels[m.rows];
  const cols = labels[m.cols];
  const lines = [['', ...cols].map(csvField).join(',')];
  rows.forEach((r, i) => {
    lines.push([csvField(r), ...cols.map((_, j) => (m.rows === m.cols && i === j ? '1' : ''))].join(','));
  });
  return `${lines.join('\n')}\n`;
}

function downloadTemplate(m, labels) {
  const blob = new Blob([templateCsv(m, labels)], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${m.name}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function Preview({ name, preview, errors }) {
  if (!preview || preview.col_labels.length === 0) return <div className="empty">Sin datos</div>;
  const byCell = new Map();
  const byRow = new Map();
  for (const e of errors) {
    if (e.row != null && e.col != null) byCell.set(`${e.row}:${e.col}`, e.message);
    else if (e.row != null) byRow.set(e.row, e.message);
  }
  const rows = preview.cells.slice(0, PREVIEW_MAX);
  const cols = preview.col_labels.slice(0, PREVIEW_MAX);
  const clipped = preview.cells.length > PREVIEW_MAX || preview.col_labels.length > PREVIEW_MAX;
  return (
    <div className="matrix-wrap">
      <table className="matrix">
        <thead>
          <tr>
            <th className="mono-sm">{preview.corner || name}</th>
            {cols.map((c, j) => <th key={j} className="mono-sm">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={byRow.has(i) ? 'row-err' : undefined} title={byRow.get(i)}>
              <th className="mono-sm">{preview.row_labels[i]}</th>
              {row.slice(0, PREVIEW_MAX).map((cell, j) => {
                const msg = byCell.get(`${i}:${j}`);
                return (
                  <td key={j} className={`mono-sm${msg ? ' cell-err' : ''}`} title={msg}>{cell}</td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {clipped && <div className="small muted">Vista parcial: primeras {PREVIEW_MAX} filas y columnas.</div>}
    </div>
  );
}

/**
 * Ingreso de CC, CE y EE (CSV o a mano) con vista previa y errores por celda (RF-15..RF-17).
 *
 * - `validate(texts)` hace el POST de validación y devuelve la Response.
 * - `labels` ({causes, effects}) fija las etiquetas: el ingreso manual no las pide y se ofrecen
 *   plantillas CSV para descargar.
 * - `validation` / `onValidation` la controla el padre, que también la actualiza si el envío
 *   final devuelve errores.
 * - `onTexts(texts | null)` informa los CSV listos para enviar (null si falta alguno).
 */
export default function MatricesInput({ validate, labels, validation, onValidation, onTexts, onMessage }) {
  const [mode, setMode] = useState('csv'); // 'csv' | 'manual'
  const [texts, setTexts] = useState({ cc: null, ce: null, ee: null });
  const [manualTexts, setManualTexts] = useState(null);
  const [fileNames, setFileNames] = useState({});
  const [validating, setValidating] = useState(false);
  const [tab, setTab] = useState('CC');

  // Ambos modos producen los mismos CSV; la validación y el envío no distinguen el origen.
  const active = mode === 'csv' ? texts : manualTexts || { cc: null, ce: null, ee: null };
  const allLoaded = MATRICES.every((m) => active[m.key] != null);

  useEffect(() => {
    onTexts(allLoaded ? active : null);
    if (!allLoaded) {
      onValidation(null);
      return undefined;
    }
    let cancelled = false;
    setValidating(true);
    validate(active)
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (res.ok) {
          onValidation(body);
          onMessage?.(null);
        } else {
          onValidation(null);
          onMessage?.(errorText(body, 'No se pudieron validar las matrices'));
        }
      })
      .catch(() => !cancelled && onMessage?.('Error de red al validar.'))
      .finally(() => !cancelled && setValidating(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, allLoaded]);

  async function onFile(key, file) {
    if (!file) {
      setTexts((t) => ({ ...t, [key]: null }));
      return;
    }
    const text = await file.text();
    setFileNames((f) => ({ ...f, [key]: file.name }));
    setTexts((t) => ({ ...t, [key]: text }));
  }

  const valid = validation?.valid === true;
  const tabErrors = validation ? validation.errors.filter((e) => e.matrix === tab) : [];
  const globalErrors = validation ? validation.errors.filter((e) => e.row == null || e.col == null) : [];
  const cellErrorCount = validation ? validation.errors.length - globalErrors.length : 0;
  const emptyCount = validation ? validation.errors.filter((e) => e.message === 'celda vacía').length : 0;

  return (
    <>
      <div className="controls" role="tablist" aria-label="Forma de ingreso" style={{ marginBottom: 0 }}>
        <button type="button" role="tab" aria-selected={mode === 'csv'} className={mode === 'csv' ? 'primary' : undefined} onClick={() => setMode('csv')}>Subir CSV</button>
        <button type="button" role="tab" aria-selected={mode === 'manual'} className={mode === 'manual' ? 'primary' : undefined} onClick={() => setMode('manual')}>Ingresar a mano</button>
      </div>

      {mode === 'manual' && <ManualMatrices onChange={setManualTexts} errors={validation?.errors} fixedLabels={labels} />}

      {mode === 'csv' && (
        <div className="grid-3">
          {MATRICES.map((m) => (
            <div className="field" key={m.key}>
              <label htmlFor={`file-${m.key}`}>{m.name} <span className="muted">({m.shape})</span></label>
              <input id={`file-${m.key}`} type="file" accept=".csv,text/csv,text/plain" onChange={(e) => onFile(m.key, e.target.files[0])} />
              <div className="hint">
                {fileNames[m.key] || m.hint}
                {labels && (
                  <>
                    {' · '}
                    <button type="button" className="link-btn" onClick={() => downloadTemplate(m, labels)}>plantilla</button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {validating && <div className="small muted">Validando…</div>}
      {validation && (
        <div className="validation">
          <div className="small">
            {valid ? (
              <span className="badge success">válido</span>
            ) : (
              <span className="badge error">{validation.errors.length} errores</span>
            )}{' '}
            <span className="muted">
              {validation.causes.length} causas · {validation.effects.length} efectos
            </span>
          </div>
          {validation.warnings.length > 0 && (
            <ul className="small notes">{validation.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
          )}
          {globalErrors.length > 0 && (
            <ul className="small notes err">
              {globalErrors.map((e, i) => (
                <li key={i}>
                  <strong>{e.matrix}</strong>
                  {e.row != null && ` fila ${e.row + 1}`}: {e.message}
                </li>
              ))}
            </ul>
          )}
          {cellErrorCount > 0 && mode === 'csv' && (
            <div className="small" style={{ color: 'var(--err)' }}>
              {cellErrorCount} celdas con errores (marcadas en rojo; pasa el cursor para ver el motivo).
            </div>
          )}
          {cellErrorCount > 0 && mode === 'manual' && (
            <div className="small" style={{ color: 'var(--err)' }}>
              {emptyCount > 0 && `${emptyCount} celdas vacías (en amarillo)`}
              {emptyCount > 0 && cellErrorCount > emptyCount && ' · '}
              {cellErrorCount > emptyCount && `${cellErrorCount - emptyCount} con valores inválidos (en rojo)`}
            </div>
          )}
          {mode === 'csv' && (
            <>
              <div className="controls" style={{ marginTop: 10 }}>
                {MATRICES.map((m) => {
                  const n = validation.errors.filter((e) => e.matrix === m.name).length;
                  return (
                    <button key={m.name} type="button" className={tab === m.name ? 'primary' : undefined} onClick={() => setTab(m.name)}>
                      {m.name}{n > 0 ? ` (${n})` : ''}
                    </button>
                  );
                })}
              </div>
              <Preview name={tab} preview={validation.previews[tab]} errors={tabErrors} />
            </>
          )}
        </div>
      )}
    </>
  );
}
