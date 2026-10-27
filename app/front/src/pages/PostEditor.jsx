import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { apiJson, send } from '../api.js';
import { errorText } from '../components/MatricesInput.jsx';
import PostView, { HOW_TO_READ } from '../components/PostView.jsx';
import { fmtDate } from './Projects.jsx';
import { PostState } from './Posts.jsx';

/**
 * Editor de una publicación del blog (RF-56 a RF-58): datos del artículo (título, autores,
 * resumen, palabras clave), secciones de texto en Markdown y figuras. Una figura se publica solo
 * si el texto la cita ({{fig:clave}}) y tiene permiso; sus descargas tienen permisos aparte.
 * «Vista previa» y «Publicar» guardan antes el borrador.
 */

const KIND_LABEL = { fe: 'Caminos', maxmin: 'MaxMin', sweep: 'Barrido' };
const VIEW_OPTIONS = {
  fe: [['sankey', 'Sankey de un orden'], ['hive', 'Hive plot (todos los órdenes)'], ['tabla', 'Tabla de caminos']],
  maxmin: [['efectos', 'Efectos por orden']],
  sweep: [['por_n', 'Paneles por N'], ['por_c', 'Paneles por c'], ['tabla', 'Tabla de estadísticas']],
};
const DEFAULT_VIEW = { fe: 'sankey', maxmin: 'efectos', sweep: 'por_n' };
const TOP_OPTIONS = [5, 10, 25, 50, 100, 200];
const INPUTS_HINT = {
  fe: 'matrices CC, CE y EE de los expertos del cálculo',
  maxmin: 'parámetros de la simulación',
  sweep: 'parámetros del barrido',
};
const RESULTS_HINT = {
  fe: 'todos los caminos de todos los órdenes',
  maxmin: 'efectos por orden',
  sweep: 'CSV crudo con cada repetición',
};
const FIG_RE = /\{\{fig:([a-z0-9_-]{1,20})\}\}/g;

function publishable(j) {
  return VIEW_OPTIONS[j.kind] && (j.status === 'success' || (j.kind === 'sweep' && j.status === 'partial'));
}

function jobDetail(j) {
  const p = j.params || {};
  if (j.kind === 'fe') {
    const by = j.expertos
      ? `agregado de ${j.expertos.length} expertos`
      : j.experto ? (j.experto.nombre || j.experto.email) : 'cálculo suelto';
    return `${by} · THR ${p.thr} · orden máx. ${p.maxorder}`;
  }
  if (j.kind === 'maxmin') return `N = ${p.numero_nodos} · c = ${p.conectividad_promedio} · THR ${p.threshold}`;
  return `${(p.ns || []).length} valores de N × ${(p.cs || []).length} de c · ${p.reps} repeticiones`;
}

function jobLink(j) {
  if (j.kind === 'fe') return `/fe/jobs/${j.job_id}`;
  if (j.kind === 'maxmin') return `/maxmin/jobs/${j.job_id}`;
  return `/sweeps/${j.job_id}`;
}

let nextKey = 1;
const withKey = (b) => ({ ...b, _k: nextKey++ });
const stripKey = ({ _k, ...b }) => b; // eslint-disable-line no-unused-vars

function draftOf(post) {
  return {
    titulo: post.titulo,
    slug: post.slug,
    resumen: post.resumen,
    autores: post.autores,
    palabras: post.palabras_clave.join(', '),
    anonimizar: post.anonimizar,
    bloques: post.bloques.map(withKey),
    figuras: post.figuras.map(withKey),
  };
}

function bodyOf(draft) {
  return {
    titulo: draft.titulo,
    slug: draft.slug,
    resumen: draft.resumen,
    autores: draft.autores,
    palabras_clave: draft.palabras.split(',').map((k) => k.trim()).filter(Boolean),
    anonimizar: draft.anonimizar,
    bloques: draft.bloques.map(stripKey),
    figuras: draft.figuras.map(stripKey),
  };
}

function nextRef(figures) {
  const used = new Set(figures.map((f) => f.ref));
  let n = 1;
  while (used.has(`f${n}`)) n += 1;
  return `f${n}`;
}

function FigureEditor({ fig, job, cited, onChange, onCite }) {
  if (!job) {
    return <p className="status err small">El cálculo {fig.job_id} ya no está disponible o aún no termina: quita esta figura o elige otro cálculo.</p>;
  }
  const p = job.params || {};
  const orders = (job.result?.orders || []).map(Number);
  const set = (field) => (e) => {
    const raw = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    const value = field === 'orden' || field === 'top' ? (raw === '' ? undefined : Number(raw)) : raw;
    const next = { ...fig, [field]: value };
    if (value === undefined || value === '') delete next[field];
    onChange(next);
  };
  const fe = job.kind === 'fe';
  return (
    <div className="post-calc-editor">
      <div className="small">
        <strong>{KIND_LABEL[job.kind]}</strong> · {jobDetail(job)}
        <span className="muted"> · job <Link to={jobLink(job)} className="mono">{job.job_id}</Link> · {fmtDate(job.created_at)}</span>
      </div>
      <div className="controls" style={{ margin: '10px 0' }}>
        <select value={fig.vista} onChange={set('vista')} aria-label="Vista">
          {VIEW_OPTIONS[job.kind].map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
        {fe && fig.vista !== 'hive' && orders.length > 0 && (
          <select value={fig.orden ?? orders[0]} onChange={set('orden')} aria-label="Orden">
            {orders.map((o) => <option key={o} value={o}>Orden {o} ({job.result?.rows_per_order?.[String(o)] ?? '?'} caminos)</option>)}
          </select>
        )}
        {fe && (
          <select value={fig.origen || ''} onChange={set('origen')} aria-label="Origen">
            <option value="">Todos los orígenes (el lector puede filtrar)</option>
            {(p.causes || []).map((c) => <option key={c} value={c}>Solo desde «{c}»</option>)}
          </select>
        )}
        {fe && (
          <select value={fig.top ?? 50} onChange={set('top')} aria-label="Caminos">
            {TOP_OPTIONS.map((n) => <option key={n} value={n}>{fig.vista === 'hive' ? `Top ${n} por orden` : `Top ${n} caminos`}</option>)}
          </select>
        )}
        {job.kind === 'maxmin' && (
          <select value={fig.escala || 'lineal'} onChange={set('escala')} aria-label="Escala">
            <option value="lineal">Escala lineal</option>
            <option value="log">Escala logarítmica</option>
          </select>
        )}
      </div>
      <div className="field">
        <input value={fig.pie || ''} onChange={set('pie')} maxLength={1000} placeholder="Leyenda de la figura: qué debería notar el lector" aria-label="Leyenda" />
      </div>
      <div className="post-perms">
        <label className="checkbox-label"><input type="checkbox" checked={!!fig.publico} onChange={set('publico')} /> Mostrar el gráfico en la publicación</label>
        <label className="checkbox-label"><input type="checkbox" checked={!!fig.descargar_entradas} onChange={set('descargar_entradas')} /> Permitir descargar las entradas ({INPUTS_HINT[job.kind]})</label>
        <label className="checkbox-label"><input type="checkbox" checked={!!fig.descargar_resultados} onChange={set('descargar_resultados')} /> Permitir descargar los resultados completos ({RESULTS_HINT[job.kind]})</label>
      </div>
      <div className="controls" style={{ margin: '10px 0 0' }}>
        <button type="button" onClick={onCite}>Citar en el texto</button>
        <span className="small muted">
          Clave <span className="mono">{`{{fig:${fig.ref}}}`}</span> ·{' '}
          {cited ? (fig.publico ? 'citada: se publicará' : <span style={{ color: 'var(--err)' }}>citada sin permiso: no se puede publicar</span>) : 'sin citar: no se publicará'}
        </span>
      </div>
      {fe && (fig.publico || fig.descargar_entradas || fig.descargar_resultados) && (
        <p className="small muted" style={{ margin: '8px 0 0' }}>
          El gráfico público recibe solo los caminos que dibuja{fig.origen ? ` desde «${fig.origen}»` : ''} (top {fig.top ?? 50}). Las descargas, si las permites, incluyen los datos completos.
        </p>
      )}
      <p className="small muted" style={{ margin: '8px 0 0' }}>
        El público verá: {HOW_TO_READ[job.kind === 'sweep' && fig.vista === 'tabla' ? 'sweep_tabla' : fig.vista]}
      </p>
    </div>
  );
}

function CalcPicker({ jobs, onPick, onClose, scope }) {
  const options = jobs.filter(publishable);
  return (
    <div className="post-picker">
      <div className="chart-head">
        <h2>Agregar una figura</h2>
        <button type="button" className="btn" onClick={onClose}>Cerrar</button>
      </div>
      <p className="sub">{scope} Solo aparecen los terminados.</p>
      {!options.length && <div className="empty">No hay cálculos terminados.</div>}
      {options.length > 0 && (
        <div style={{ overflow: 'auto', maxHeight: 380 }}>
          <table>
            <thead><tr><th>Tipo</th><th>Detalle</th><th>Fecha</th><th /></tr></thead>
            <tbody>
              {options.map((j) => (
                <tr key={j.job_id}>
                  <td>{KIND_LABEL[j.kind]}{j.status === 'partial' && <span className="small muted"> (parcial)</span>}</td>
                  <td className="small">{jobDetail(j)}</td>
                  <td className="small muted">{fmtDate(j.created_at)}</td>
                  <td><button type="button" className="btn primary" onClick={() => onPick(j)}>Agregar</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function PostEditor() {
  const { postId } = useParams();
  const navigate = useNavigate();
  const [post, setPost] = useState(null);
  const [draft, setDraft] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [jobs, setJobs] = useState(null);
  const [mode, setMode] = useState('editar');
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState({ text: '', err: false });
  const [picker, setPicker] = useState(false);
  const [loadError, setLoadError] = useState('');
  const lastText = useRef({ index: null, pos: null }); // última sección y posición del cursor

  useEffect(() => {
    send(`/app/posts/${postId}`, 'GET').then(({ ok, data }) => {
      if (!ok) {
        setLoadError(errorText(data, 'No se pudo cargar la publicación'));
        return;
      }
      setPost(data);
      setDraft(draftOf(data));
      const path = data.proyecto ? `/app/projects/${data.proyecto.id}/jobs` : '/app/jobs';
      apiJson(path).then((d) => setJobs(Array.isArray(d) ? d : [])).catch(() => setJobs([]));
    }).catch(() => setLoadError('Error de red'));
  }, [postId]);

  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (e) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const jobsById = useMemo(() => Object.fromEntries((jobs || []).filter(publishable).map((j) => [j.job_id, j])), [jobs]);
  const cited = useMemo(
    () => new Set((draft?.bloques || []).flatMap((b) => [...b.markdown.matchAll(FIG_RE)].map((m) => m[1]))),
    [draft],
  );

  function change(fields) {
    setDraft((d) => ({ ...d, ...fields }));
    setDirty(true);
  }
  function setList(key, fn) {
    setDraft((d) => ({ ...d, [key]: fn(d[key]) }));
    setDirty(true);
  }
  const updateItem = (key, i) => (b) => setList(key, (bs) => bs.map((x, j) => (j === i ? b : x)));
  const removeItem = (key, i) => setList(key, (bs) => bs.filter((_, j) => j !== i));
  const moveItem = (key, i, d) => setList(key, (bs) => {
    const next = [...bs];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    return next;
  });

  /** Inserta la cita en la sección y posición donde estaba el cursor (o al final del texto). */
  function cite(ref) {
    const token = `{{fig:${ref}}}`;
    setList('bloques', (bs) => {
      if (!bs.length) return [withKey({ tipo: 'texto', markdown: token })];
      const { index, pos } = lastText.current;
      const i = index != null && index < bs.length ? index : bs.length - 1;
      const text = bs[i].markdown;
      const at = pos != null && pos <= text.length ? pos : text.length;
      const sep = at > 0 && !/\s$/.test(text.slice(0, at)) ? ' ' : '';
      return bs.map((b, j) => (j === i ? { ...b, markdown: `${text.slice(0, at)}${sep}${token}${text.slice(at)}` } : b));
    });
    report(`Cita ${token} insertada en el texto.`);
  }

  function report(text, err = false) {
    setMsg({ text, err });
  }

  async function save() {
    if (!dirty) return true;
    const { ok, data } = await send(`/app/posts/${postId}`, 'PATCH', bodyOf(draft)).catch(() => ({ ok: false, data: {} }));
    if (!ok) {
      report(errorText(data, 'No se pudo guardar'), true);
      return false;
    }
    setPost(data);
    setDraft((d) => ({ ...d, slug: data.slug }));
    setDirty(false);
    return true;
  }

  async function run(label, fn) {
    setBusy(label);
    report('');
    try {
      await fn();
    } finally {
      setBusy('');
    }
  }

  const onSave = () => run('guardar', async () => {
    if (await save()) report('Borrador guardado.');
  });

  const onPreview = () => run('vista', async () => {
    if (!(await save())) return;
    const { ok, data } = await send(`/app/posts/${postId}/preview`, 'GET').catch(() => ({ ok: false, data: {} }));
    if (!ok) {
      report(errorText(data, 'No se pudo armar la vista previa'), true);
      return;
    }
    setPreview(data);
    setMode('vista');
  });

  const onPublish = () => run('publicar', async () => {
    if (!(await save())) return;
    const { ok, data } = await send(`/app/posts/${postId}/publish`, 'POST').catch(() => ({ ok: false, data: {} }));
    if (!ok) {
      report(errorText(data, 'No se pudo publicar'), true);
      return;
    }
    setPost(data);
    report('Publicada. El público ya ve esta versión.');
  });

  const onUnpublish = () => run('retirar', async () => {
    if (!window.confirm('¿Retirar la publicación del blog? También se cortan las descargas. El borrador se conserva.')) return;
    const { ok, data } = await send(`/app/posts/${postId}/unpublish`, 'POST').catch(() => ({ ok: false, data: {} }));
    if (ok) {
      setPost(data);
      report('Retirada del blog.');
    } else report(errorText(data, 'No se pudo retirar'), true);
  });

  const onDelete = () => run('borrar', async () => {
    if (!window.confirm(`¿Borrar «${post.titulo}»? No se puede deshacer.`)) return;
    const { ok, data } = await send(`/app/posts/${postId}`, 'DELETE').catch(() => ({ ok: false, data: {} }));
    if (ok) {
      setDirty(false);
      navigate(post.proyecto ? `/projects/${post.proyecto.id}/blog` : '/posts');
    } else report(errorText(data, 'No se pudo borrar'), true);
  });

  if (loadError) {
    return <div className="card"><p className="status err">{loadError}</p><Link to="/posts" className="small">← blog</Link></div>;
  }
  if (!post || !draft) return <div className="card"><div className="empty">Cargando…</div></div>;

  const published = post.estado === 'publicado';
  const ItemBar = ({ list, i, label }) => (
    <div className="post-block-bar">
      <span className="small muted">{label}</span>
      <div className="controls" style={{ marginBottom: 0 }}>
        <button type="button" onClick={() => moveItem(list, i, -1)} disabled={i === 0} aria-label="Subir">↑</button>
        <button type="button" onClick={() => moveItem(list, i, 1)} disabled={i === draft[list].length - 1} aria-label="Bajar">↓</button>
        <button type="button" onClick={() => removeItem(list, i)}>Quitar</button>
      </div>
    </div>
  );

  return (
    <div className="stack">
      <div className="card post-editor-head">
        <div className="small">
          {post.proyecto
            ? <Link to={`/projects/${post.proyecto.id}/blog`} className="muted">← publicaciones de {post.proyecto.nombre}</Link>
            : <Link to="/posts" className="muted">← blog</Link>}
        </div>
        <div className="chart-head" style={{ marginTop: 6 }}>
          <div>
            <h1 style={{ marginBottom: 4 }}>{draft.titulo || 'Sin título'}</h1>
            <div className="small muted">
              <PostState post={post} />
              {published && <> · publicada el {fmtDate(post.publicado_at)} · <a href={`/front/blog/${post.slug}`} target="_blank" rel="noreferrer">ver en el blog ↗</a></>}
              {dirty && <> · <span style={{ color: 'var(--warn)' }}>cambios sin guardar</span></>}
            </div>
          </div>
          <div className="controls" style={{ marginBottom: 0 }}>
            <button type="button" className={mode === 'editar' ? 'primary' : undefined} aria-pressed={mode === 'editar'} onClick={() => setMode('editar')}>Editar</button>
            <button type="button" className={mode === 'vista' ? 'primary' : undefined} aria-pressed={mode === 'vista'} onClick={onPreview} disabled={!!busy}>
              {busy === 'vista' ? 'Armando…' : 'Vista previa'}
            </button>
            <button type="button" onClick={onSave} disabled={!dirty || !!busy}>{busy === 'guardar' ? 'Guardando…' : 'Guardar'}</button>
            <button type="button" className="primary" onClick={onPublish} disabled={!!busy || (published && !dirty && !post.cambios_sin_publicar)}>
              {busy === 'publicar' ? 'Publicando…' : published ? 'Publicar cambios' : 'Publicar'}
            </button>
          </div>
        </div>
        {msg.text && <p className={`status small ${msg.err ? 'err' : 'ok'}`} style={{ marginBottom: 0 }}>{msg.text}</p>}
      </div>

      {mode === 'vista' && preview && (
        <>
          {preview.avisos.length > 0 && (
            <div className="card">
              <h2>Antes de publicar</h2>
              <ul className="notes">{preview.avisos.map((a) => <li key={a}>{a}</li>)}</ul>
            </div>
          )}
          <div className="card post-preview"><PostView post={preview} /></div>
        </>
      )}

      {mode === 'editar' && (
        <>
          <div className="card">
            <h2 style={{ marginBottom: 12 }}>Artículo</h2>
            <div className="grid-2" style={{ marginBottom: 14, alignItems: 'start' }}>
              <div className="field">
                <label htmlFor="post-titulo">Título</label>
                <input id="post-titulo" value={draft.titulo} maxLength={200} onChange={(e) => change({ titulo: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="post-slug">Dirección</label>
                <input id="post-slug" value={draft.slug} maxLength={80} onChange={(e) => change({ slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-') })} />
                <span className="hint">/front/blog/{draft.slug || '…'}</span>
              </div>
              <div className="field">
                <label htmlFor="post-autores">Autores</label>
                <input id="post-autores" value={draft.autores} maxLength={300} placeholder="A. Pérez, B. Soto" onChange={(e) => change({ autores: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="post-palabras">Palabras clave</label>
                <input id="post-palabras" value={draft.palabras} placeholder="efectos olvidados, retail" onChange={(e) => change({ palabras: e.target.value })} />
                <span className="hint">Separadas por comas (hasta 10).</span>
              </div>
            </div>
            <div className="field" style={{ marginBottom: 14 }}>
              <label htmlFor="post-resumen">Resumen</label>
              <textarea id="post-resumen" rows={4} maxLength={3000} value={draft.resumen} onChange={(e) => change({ resumen: e.target.value })} placeholder="Objetivo, método, resultados principales y conclusión, en un párrafo. Aparece también en la lista del blog." />
            </div>
            {post.proyecto?.tipo === 'expertos' && (
              <label className="checkbox-label">
                <input type="checkbox" checked={!draft.anonimizar} onChange={(e) => change({ anonimizar: !e.target.checked })} />
                Mostrar el nombre de los expertos (por defecto aparecen como «Experto 1», «Experto 2»…; el correo nunca se publica)
              </label>
            )}
          </div>

          <h2 className="post-editor-section">Texto</h2>
          {draft.bloques.map((b, i) => (
            <div key={b._k} className="card post-block">
              <ItemBar list="bloques" i={i} label={`Sección ${i + 1}`} />
              <div className="field">
                <textarea
                  rows={Math.min(24, Math.max(4, b.markdown.split('\n').length + 1))}
                  value={b.markdown}
                  onChange={(e) => {
                    lastText.current = { index: i, pos: e.target.selectionStart };
                    updateItem('bloques', i)({ ...b, markdown: e.target.value });
                  }}
                  onSelect={(e) => { lastText.current = { index: i, pos: e.target.selectionStart }; }}
                  placeholder="## Título de la sección&#10;&#10;Texto. Cita figuras con {{fig:f1}}."
                  aria-label={`Sección ${i + 1}`}
                />
              </div>
            </div>
          ))}
          <div className="card">
            <div className="controls" style={{ marginBottom: 0 }}>
              <button type="button" onClick={() => setList('bloques', (bs) => [...bs, withKey({ tipo: 'texto', markdown: '## ' })])}>Agregar sección</button>
              <details className="small">
                <summary className="muted">Formato del texto (Markdown)</summary>
                <table className="post-md-help">
                  <tbody>
                    <tr><td className="mono">## Título</td><td>título de sección (se numera solo)</td></tr>
                    <tr><td className="mono">{'{{fig:f1}}'}</td><td>cita a una figura («Figura 1»)</td></tr>
                    <tr><td className="mono">**negrita**, *cursiva*</td><td>énfasis</td></tr>
                    <tr><td className="mono">- elemento</td><td>lista</td></tr>
                    <tr><td className="mono">[texto](https://…)</td><td>enlace o referencia</td></tr>
                    <tr><td className="mono">&gt; cita</td><td>cita textual</td></tr>
                    <tr><td className="mono">| a | b |</td><td>tabla</td></tr>
                  </tbody>
                </table>
              </details>
            </div>
          </div>

          <h2 className="post-editor-section">Figuras y datos</h2>
          <p className="sub" style={{ margin: 0 }}>
            Las figuras se muestran en la barra lateral, numeradas en el orden en que el texto las cita. Una figura se publica solo si
            está citada y tiene permiso; los datos crudos se descargan solo con su permiso.
          </p>
          {draft.figuras.map((f, i) => (
            <div key={f._k} className="card post-block">
              <ItemBar list="figuras" i={i} label={`Figura «${f.ref}»`} />
              {jobs
                ? <FigureEditor fig={f} job={jobsById[f.job_id]} cited={cited.has(f.ref)} onChange={updateItem('figuras', i)} onCite={() => cite(f.ref)} />
                : <p className="small muted">Cargando el cálculo {f.job_id}…</p>}
            </div>
          ))}
          {picker ? (
            <div className="card">
              <CalcPicker
                jobs={jobs || []}
                scope={post.proyecto ? `Cálculos del proyecto «${post.proyecto.nombre}».` : 'Cálculos que puedes ver.'}
                onClose={() => setPicker(false)}
                onPick={(j) => {
                  setList('figuras', (fs) => [...fs, withKey({ ref: nextRef(fs), job_id: j.job_id, vista: DEFAULT_VIEW[j.kind], publico: false })]);
                  setPicker(false);
                }}
              />
            </div>
          ) : (
            <div className="card">
              <button type="button" className="btn" onClick={() => setPicker(true)}>Agregar figura de un cálculo</button>
            </div>
          )}

          <div className="controls">
            {published && <button type="button" onClick={onUnpublish} disabled={!!busy}>Retirar del blog</button>}
            <button type="button" onClick={onDelete} disabled={!!busy}>Borrar publicación</button>
          </div>
        </>
      )}
    </div>
  );
}
