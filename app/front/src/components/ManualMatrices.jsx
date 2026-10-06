import { useEffect, useMemo, useState } from 'react';

export const MANUAL_MAX_LABELS = 30; // por lado; matrices más grandes conviene subirlas como CSV

const MATRICES = [
  { name: 'CC', rows: 'causes', cols: 'causes', title: 'CC · causas × causas' },
  { name: 'CE', rows: 'causes', cols: 'effects', title: 'CE · causas × efectos' },
  { name: 'EE', rows: 'effects', cols: 'effects', title: 'EE · efectos × efectos' },
];

function parseLabels(text) {
  return text.split('\n').map((l) => l.trim()).filter(Boolean);
}

function csvField(value) {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

function cellKey(matrix, row, col) {
  return `${matrix}\u0000${row}\u0000${col}`;
}

function isDiagonal(m, i, j) {
  return m.rows === m.cols && i === j;
}

/** Errores propios de las etiquetas, antes de mandar nada al backend. */
function labelProblems(causes, effects) {
  const problems = [];
  const dup = (list) => list.filter((l, i) => list.indexOf(l) !== i);
  if (causes.length === 0) problems.push('Ingresa al menos una causa.');
  if (effects.length === 0) problems.push('Ingresa al menos un efecto.');
  if (causes.length > MANUAL_MAX_LABELS || effects.length > MANUAL_MAX_LABELS) {
    problems.push(`Máximo ${MANUAL_MAX_LABELS} causas y ${MANUAL_MAX_LABELS} efectos a mano; para matrices más grandes sube los CSV.`);
  }
  const repeated = [...new Set([...dup(causes), ...dup(effects)])];
  if (repeated.length) problems.push(`Etiquetas repetidas: ${repeated.join(', ')}.`);
  const shared = causes.filter((c) => effects.includes(c));
  if (shared.length) problems.push(`Una etiqueta no puede ser causa y efecto a la vez: ${shared.join(', ')}.`);
  if ([...causes, ...effects].some((l) => l.includes(';'))) problems.push("Las etiquetas no pueden contener ';'.");
  return problems;
}

/**
 * Ingreso manual de CC, CE y EE. Genera los mismos CSV que se subirían como archivo, así que la
 * validación y el lanzamiento son los del flujo de CSV. La diagonal de CC y EE queda fija en 1.
 */
export default function ManualMatrices({ onChange, errors }) {
  const [causesText, setCausesText] = useState('');
  const [effectsText, setEffectsText] = useState('');
  const [values, setValues] = useState({}); // cellKey -> texto, sobrevive a reordenar etiquetas
  const causes = useMemo(() => parseLabels(causesText), [causesText]);
  const effects = useMemo(() => parseLabels(effectsText), [effectsText]);
  const labels = { causes, effects };
  const problems = labelProblems(causes, effects);

  useEffect(() => {
    if (problems.length) {
      onChange(null);
      return undefined;
    }
    // Debounce: no validar en el backend con cada tecla.
    const timer = setTimeout(() => {
      const csv = {};
      for (const m of MATRICES) {
        const rows = labels[m.rows];
        const cols = labels[m.cols];
        const lines = [['', ...cols].map(csvField).join(',')];
        rows.forEach((r, i) => {
          const cells = cols.map((c, j) => {
            if (isDiagonal(m, i, j)) return '1';
            return (values[cellKey(m.name, r, c)] || '').trim().replace(',', '.');
          });
          lines.push([csvField(r), ...cells].join(','));
        });
        csv[m.name.toLowerCase()] = `${lines.join('\n')}\n`;
      }
      onChange(csv);
    }, 400);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [causesText, effectsText, values]);

  function setCell(matrix, row, col, value) {
    setValues((v) => ({ ...v, [cellKey(matrix, row, col)]: value }));
  }

  function fillEmpty(m) {
    setValues((v) => {
      const next = { ...v };
      labels[m.rows].forEach((r, i) => labels[m.cols].forEach((c, j) => {
        const key = cellKey(m.name, r, c);
        if (!isDiagonal(m, i, j) && !(next[key] || '').trim()) next[key] = '0';
      }));
      return next;
    });
  }

  /** Pegar un bloque copiado de Excel (tabs y saltos de línea) a partir de la celda (i, j). */
  function onPaste(e, m, i, j) {
    const text = e.clipboardData.getData('text');
    if (!/[\t\n]/.test(text)) return;
    e.preventDefault();
    const block = text.replace(/\r/g, '').replace(/\n$/, '').split('\n').map((line) => line.split('\t'));
    setValues((v) => {
      const next = { ...v };
      block.forEach((cells, di) => cells.forEach((cell, dj) => {
        const r = labels[m.rows][i + di];
        const c = labels[m.cols][j + dj];
        if (r !== undefined && c !== undefined && !isDiagonal(m, i + di, j + dj)) {
          next[cellKey(m.name, r, c)] = cell.trim();
        }
      }));
      return next;
    });
  }

  /** Enter baja a la fila siguiente, como en una planilla. */
  function onKeyDown(e, m, i, j) {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    // Salta la diagonal (deshabilitada) y sigue hacia abajo.
    for (let k = i + 1; ; k += 1) {
      const next = document.getElementById(`cell-${m.name}-${k}-${j}`);
      if (!next) return;
      if (!next.disabled) {
        next.focus();
        return;
      }
    }
  }

  const errorsByCell = new Map();
  for (const err of errors || []) {
    if (err.row != null && err.col != null) errorsByCell.set(`${err.matrix}:${err.row}:${err.col}`, err.message);
  }

  return (
    <div className="manual">
      <div className="grid-2">
        <div className="field">
          <label htmlFor="causes">Causas ({causes.length})</label>
          <textarea id="causes" rows={5} placeholder={'Una por línea, ej.\nPrecio\nPublicidad'} value={causesText} onChange={(e) => setCausesText(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="effects">Efectos ({effects.length})</label>
          <textarea id="effects" rows={5} placeholder={'Una por línea, ej.\nVentas\nCostos'} value={effectsText} onChange={(e) => setEffectsText(e.target.value)} />
        </div>
      </div>

      {problems.length > 0 && (causesText || effectsText) && (
        <ul className="small notes err">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
      )}

      {problems.length === 0 && MATRICES.map((m) => (
        <div key={m.name} className="manual-matrix">
          <div className="manual-head">
            <h2>{m.title}</h2>
            <button type="button" className="btn" onClick={() => fillEmpty(m)}>Rellenar vacías con 0</button>
          </div>
          <div className="matrix-wrap">
            <table className="matrix">
              <thead>
                <tr>
                  <th aria-hidden="true" />
                  {labels[m.cols].map((c) => <th key={c} className="mono-sm">{c}</th>)}
                </tr>
              </thead>
              <tbody>
                {labels[m.rows].map((r, i) => (
                  <tr key={r}>
                    <th className="mono-sm">{r}</th>
                    {labels[m.cols].map((c, j) => {
                      const diag = isDiagonal(m, i, j);
                      const msg = errorsByCell.get(`${m.name}:${i}:${j}`);
                      const empty = msg === 'celda vacía';
                      return (
                        <td key={c} className={msg && !empty ? 'cell-err' : undefined} title={msg}>
                          <input
                            id={`cell-${m.name}-${i}-${j}`}
                            className={`cell-input${empty ? ' cell-empty' : ''}`}
                            inputMode="decimal"
                            aria-label={`${m.name} ${r} → ${c}`}
                            value={diag ? '1' : values[cellKey(m.name, r, c)] || ''}
                            disabled={diag}
                            onChange={(e) => setCell(m.name, r, c, e.target.value)}
                            onPaste={(e) => onPaste(e, m, i, j)}
                            onKeyDown={(e) => onKeyDown(e, m, i, j)}
                          />
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
      {problems.length === 0 && (
        <p className="small muted" style={{ margin: 0 }}>
          Valores en [0,1], con punto o coma decimal. La diagonal de CC y EE vale 1. Puedes pegar un bloque copiado de Excel
          en cualquier celda; Enter baja a la fila siguiente.
        </p>
      )}
    </div>
  );
}
