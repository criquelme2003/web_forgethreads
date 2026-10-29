import { useEffect, useMemo, useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import EffectsChart, { formatCount } from './EffectsChart.jsx';
import PathsHive from './PathsHive.jsx';
import PathsSankey from './PathsSankey.jsx';
import { fmtValue, tablesToPaths } from './pathsModel.js';
import { ByC, ByN, fmt, RegimeLegend } from './SweepCharts.jsx';
import VizErrorBoundary from './VizErrorBoundary.jsx';

/**
 * Publicación del blog tal como la ve el público (y la vista previa del editor), con formato de
 * artículo: cabecera, resumen, palabras clave y secciones numeradas en la columna principal, y una
 * barra lateral con la ficha del proyecto, las figuras (numeradas por orden de cita) y los datos
 * descargables. Recibe la instantánea de `/app/blog/<slug>` o `/app/posts/<id>/preview`.
 * El Markdown se muestra sin HTML crudo (react-markdown no lo interpreta).
 */

export const TIPO_PROYECTO = { expertos: 'Consulta a expertos', simulacion: 'Simulación' };

export function fmtDay(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString('es-CL', { day: 'numeric', month: 'long', year: 'numeric' });
  } catch {
    return iso;
  }
}

function who(meta) {
  if (!meta.expertos?.length) return '';
  return meta.agregado ? `${meta.expertos.length} expertos en conjunto` : meta.expertos.join(', ');
}

/** Título automático de un gráfico a partir de su cálculo y su vista. */
export function calcTitle(b) {
  if (b.pendiente) return b.descripcion || (b.estado === 'sin_resultados' ? 'Figura sin resultados' : 'Figura en preparación');
  if (b.kind === 'fe') {
    const by = who(b.meta);
    const suffix = by ? ` · ${by}` : '';
    const from = b.origen ? ` desde «${b.origen}»` : '';
    if (b.vista === 'hive') return `Caminos indirectos de todos los órdenes${from}${suffix}`;
    return `${b.vista === 'tabla' ? 'Tabla de caminos' : 'Caminos indirectos'} de orden ${b.orden}${from}${suffix}`;
  }
  if (b.kind === 'maxmin') return `Efectos encontrados por orden · N = ${b.meta.n ?? '—'}, c = ${b.meta.c ?? '—'}`;
  if (b.vista === 'por_n') return 'Orden efectivo medio según la conectividad c, para cada tamaño N';
  if (b.vista === 'por_c') return 'Orden efectivo medio según el tamaño N, para cada conectividad c';
  return 'Resultados del barrido por combinación de N y c';
}

/** Explicación breve para quien no conoce el gráfico. */
export const HOW_TO_READ = {
  sankey:
    'Cada franja es un camino indirecto: parte en una causa (izquierda), pasa por factores intermedios y llega a un efecto (derecha). '
    + 'El orden es la cantidad de pasos del camino. Mientras más ancha la franja, más fuerte es la influencia (Mean, de 0 a 1).',
  hive:
    'Hay tres ejes: las causas donde parte cada camino (Origen), los factores por donde pasa (Intermedios) y los efectos donde llega (Destino). '
    + 'Cada curva es un camino; los tonos más oscuros son caminos más largos, es decir, más indirectos, y el grosor indica su fuerza.',
  tabla:
    'Cada fila es un camino indirecto: dónde parte (From), por dónde pasa (Through) y dónde llega (To). '
    + 'Mean es la fuerza media del efecto, de 0 a 1. En los cálculos con varios expertos, Count y SD resumen el remuestreo (bootstrap).',
  efectos:
    'La altura de cada barra es la cantidad de efectos encontrados en ese orden de la composición MaxMin. '
    + 'Cuando dejan de aparecer efectos nuevos, la relación se estabilizó: ese es el orden efectivo.',
  por_n:
    'Cada panel es un tamaño de red N. Cada punto es el promedio del orden efectivo en las repeticiones con esa conectividad c, '
    + 'con su error estándar; la forma del punto indica el rango de c.',
  por_c:
    'Cada panel es una conectividad c. La línea es el promedio del orden efectivo según el tamaño N y la banda va del mínimo al máximo observado.',
  sweep_tabla:
    'Por cada combinación de tamaño N y conectividad c: promedio, desviación estándar, error estándar, mínimo y máximo del orden efectivo.',
};

function PathsTable({ table }) {
  const meanIdx = table.columns.indexOf('Mean');
  return (
    <div className="post-table">
      <table>
        <thead><tr>{table.columns.map((c) => <th key={c} className={typeof table.rows[0]?.[table.columns.indexOf(c)] === 'number' ? 'num' : undefined}>{c}</th>)}</tr></thead>
        <tbody>
          {table.rows.map((r, i) => (
            <tr key={i}>{r.map((v, j) => <td key={j} className={typeof v === 'number' ? `num${j === meanIdx ? ' mono' : ''}` : undefined}>{typeof v === 'number' ? fmtValue(v) : v}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FeChart({ b, compact }) {
  const { datos } = b;
  // Sin origen guardado, el lector puede filtrar con un clic en una causa.
  const [origin, setOrigin] = useState('');
  const onOrigin = b.origen ? undefined : setOrigin;
  const current = b.origen || origin;
  const tables = datos.tables;
  const paths = useMemo(() => (b.vista === 'hive' ? tablesToPaths(tables, 'Mean') : []), [b.vista, tables]);
  if (!tables.length || tables.every((t) => !t.rows.length)) {
    return <div className="empty">No se encontraron caminos indirectos con el umbral usado (THR = {b.meta.thr}).</div>;
  }
  const shown = tables.reduce((s, t) => s + t.rows.length, 0);
  const total = tables.reduce((s, t) => s + t.total, 0);
  let chart;
  if (b.vista === 'hive') {
    chart = (
      <PathsHive
        paths={origin ? paths.filter((p) => p.from === origin) : paths}
        orders={datos.orders}
        causes={datos.causes}
        effects={datos.effects}
        metric="Mean"
        origin={current}
        onOriginChange={onOrigin}
      />
    );
  } else if (b.vista === 'sankey') {
    const t = tables[0];
    chart = (
      <PathsSankey
        columns={t.columns}
        rows={origin ? t.rows.filter((r) => r[0] === origin) : t.rows}
        metric="Mean"
        causes={datos.causes}
        origin={current}
        onOriginChange={onOrigin}
        minLayerWidth={compact ? 90 : undefined}
      />
    );
  } else {
    chart = <PathsTable table={tables[0]} />;
  }
  return (
    <>
      {onOrigin && b.vista !== 'tabla' && (
        <p className="small muted post-hint">
          {origin ? <>Mostrando solo los caminos que parten en «{origin}». <button type="button" className="link-btn" onClick={() => setOrigin('')}>Ver todos</button></> : 'Haz clic en una causa de origen para ver solo sus caminos.'}
        </p>
      )}
      <VizErrorBoundary resetKey={`${b.vista}|${origin}`}>{chart}</VizErrorBoundary>
      {total > shown && (
        <p className="small muted post-hint">
          Se muestran los {shown} caminos más fuertes de {total}{b.vista === 'hive' ? ` (hasta ${b.top} por orden)` : ''}.
        </p>
      )}
    </>
  );
}

function SweepTable({ stats }) {
  return (
    <div className="post-table">
      <table>
        <thead><tr><th className="num">N</th><th className="num">c</th><th className="num">Media</th><th className="num">Desv. est.</th><th className="num">Error est.</th><th className="num">Mín</th><th className="num">Máx</th><th className="num">Rep.</th></tr></thead>
        <tbody>
          {stats.map((s) => (
            <tr key={`${s.n}|${s.c}`}>
              <td className="num">{s.n}</td><td className="num">{fmt(s.c)}</td><td className="num">{fmt(s.media)}</td>
              <td className="num">{fmt(s.desviacion_estandar)}</td><td className="num">{fmt(s.error_estandar)}</td>
              <td className="num">{s.minimo}</td><td className="num">{s.maximo}</td><td className="num">{s.repeticiones}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CalcMeta({ b }) {
  if (b.pendiente) return null;
  const m = b.meta;
  const items = [];
  if (b.kind === 'fe') {
    if (m.expertos?.length) items.push(m.agregado ? `Expertos: ${m.expertos.join(', ')}` : `Experto: ${m.expertos[0]}`);
    items.push(`THR ${m.thr}`, `orden máximo ${m.maxorder}`);
    if (m.reps) items.push(`${formatCount(m.reps)} repeticiones (bootstrap)`);
  } else if (b.kind === 'maxmin') {
    items.push(`N = ${m.n}`, `c = ${m.c}`, `THR ${m.thr}`);
    if (m.orden_efectivo != null) items.push(`orden efectivo ${m.orden_efectivo}`);
  } else {
    items.push(`THR ${m.thr}`, `${m.reps} repeticiones por combinación`);
    if (m.parcial) items.push('barrido parcial (se cortó antes de terminar)');
  }
  items.push(`calculado el ${fmtDay(b.fecha)}`);
  return <p className="post-figure-meta">{items.join(' · ')}</p>;
}

/** Gráfico de una figura (en la barra lateral o ampliada). */
function FigureChart({ b, logY, compact }) {
  if (b.pendiente) {
    return b.estado === 'sin_resultados' ? (
      <div className="post-figure-pending">
        <strong>Sin resultados</strong>
        <span>El cálculo de esta figura terminó sin resultados. Los autores pueden repetirlo con otros parámetros.</span>
      </div>
    ) : (
      <div className="post-figure-pending">
        <strong>Figura en preparación</strong>
        <span>El cálculo de esta figura aún no está disponible. Aparecerá cuando los autores actualicen la publicación.</span>
      </div>
    );
  }
  const stats = b.datos.estadisticas || [];
  if (b.kind === 'fe') return <FeChart b={b} compact={compact} />;
  if (b.kind === 'maxmin') {
    return b.datos.effects_by_order.length
      ? <EffectsChart data={b.datos.effects_by_order} scale={b.escala === 'log' ? 'log' : 'linear'} />
      : <div className="empty">El cálculo no registró efectos por orden.</div>;
  }
  if (!stats.length) return <div className="empty">El barrido no tiene combinaciones terminadas.</div>;
  return (
    <>
      {b.vista !== 'tabla' && <RegimeLegend />}
      <VizErrorBoundary resetKey={`${b.vista}|${logY}`}>
        {b.vista === 'por_n' && <ByN stats={stats} logY={logY} />}
        {b.vista === 'por_c' && <ByC stats={stats} logY={logY} />}
        {b.vista === 'tabla' && <SweepTable stats={stats} />}
      </VizErrorBoundary>
    </>
  );
}

export function figureHref(slug, numero, archivo) {
  return `/app/blog/${encodeURIComponent(slug)}/figuras/${numero}/${archivo}.csv`;
}

const DOWNLOAD_LABEL = { entradas: 'Entradas (CSV)', resultados: 'Resultados completos (CSV)' };

function Downloads({ b, slug }) {
  if (!b.descargas?.length) return null;
  return (
    <div className="post-downloads">
      {b.descargas.map((d) => (
        slug
          ? <a key={d} className="btn" href={figureHref(slug, b.numero, d)} download>↓ {DOWNLOAD_LABEL[d]}</a>
          : <span key={d} className="btn" title="Disponible al publicar">↓ {DOWNLOAD_LABEL[d]}</span>
      ))}
    </div>
  );
}

/**
 * Figura en la lista de la barra lateral: cerrada muestra solo su número y título; abierta, el
 * gráfico compacto, la leyenda y las descargas. «Ampliar» la muestra a todo el ancho.
 */
function SideFigure({ b, slug, open, highlighted, onToggle, onExpand }) {
  const [logY, setLogY] = useState(false);
  const howKey = b.kind === 'sweep' && b.vista === 'tabla' ? 'sweep_tabla' : b.vista;
  return (
    <li id={`figura-${b.numero}`} className={`post-figure${open ? ' is-open' : ''}${highlighted ? ' is-highlighted' : ''}`}>
      <button type="button" className="post-figure-toggle" aria-expanded={open} onClick={onToggle}>
        <span className="post-figure-num">Figura {b.numero}</span>
        <span className="post-figure-title">{calcTitle(b)}</span>
        <span className="post-figure-chevron" aria-hidden="true">{open ? '−' : '+'}</span>
      </button>
      {open && (
        <div className="post-figure-body">
          <CalcMeta b={b} />
          {b.kind === 'sweep' && b.vista !== 'tabla' && !b.pendiente && (
            <label className="checkbox-label"><input type="checkbox" checked={logY} onChange={(e) => setLogY(e.target.checked)} /> Eje vertical logarítmico</label>
          )}
          {!b.pendiente && (
            <details className="post-howto">
              <summary>¿Cómo leer este gráfico?</summary>
              <p>{HOW_TO_READ[howKey]}</p>
            </details>
          )}
          <div className="post-figure-chart"><FigureChart b={b} logY={logY} compact /></div>
          {b.pie && <figcaption><strong>Figura {b.numero}.</strong> {b.pie}</figcaption>}
          {!b.pendiente && (
            <div className="post-figure-actions">
              <button type="button" className="btn" onClick={() => onExpand(b)}>⤢ Ampliar</button>
              <Downloads b={b} slug={slug} />
            </div>
          )}
        </div>
      )}
    </li>
  );
}

/** Figura a todo el ancho: en el cuerpo del texto, en la versión para imprimir y en el editor. */
export function FigureBlock({ b, className = '' }) {
  const [logY, setLogY] = useState(false);
  const howKey = b.kind === 'sweep' && b.vista === 'tabla' ? 'sweep_tabla' : b.vista;
  return (
    <figure className={`post-figure-block ${className}`}>
      {b.numero > 0 && <span className="post-figure-num">Figura {b.numero}</span>}
      <h3>{calcTitle(b)}</h3>
      <CalcMeta b={b} />
      {b.kind === 'sweep' && b.vista !== 'tabla' && !b.pendiente && (
        <label className="checkbox-label no-print"><input type="checkbox" checked={logY} onChange={(e) => setLogY(e.target.checked)} /> Eje vertical logarítmico</label>
      )}
      {!b.pendiente && (
        <details className="post-howto no-print">
          <summary>¿Cómo leer este gráfico?</summary>
          <p>{HOW_TO_READ[howKey]}</p>
        </details>
      )}
      <FigureChart b={b} logY={logY} />
      {b.pie && <figcaption>{b.numero > 0 && <strong>Figura {b.numero}. </strong>}{b.pie}</figcaption>}
    </figure>
  );
}

function ExpandedFigure({ b, onClose }) {
  const [logY, setLogY] = useState(false);
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="post-modal" role="dialog" aria-modal="true" aria-label={`Figura ${b.numero}`} onClick={onClose}>
      <div className="post-modal-body" onClick={(e) => e.stopPropagation()}>
        <div className="post-figure-head">
          <span className="post-figure-num">Figura {b.numero}</span>
          <button type="button" className="btn" onClick={onClose}>Cerrar</button>
        </div>
        <h3>{calcTitle(b)}</h3>
        <CalcMeta b={b} />
        {b.kind === 'sweep' && b.vista !== 'tabla' && !b.pendiente && (
          <label className="checkbox-label"><input type="checkbox" checked={logY} onChange={(e) => setLogY(e.target.checked)} /> Eje vertical logarítmico</label>
        )}
        <FigureChart b={b} logY={logY} />
        {b.pie && <figcaption><strong>Figura {b.numero}.</strong> {b.pie}</figcaption>}
      </div>
    </div>
  );
}

function ProjectFacts({ f }) {
  const expert = f.tipo === 'expertos';
  return (
    <details className="post-side-card">
      <summary>
        <span className="post-side-title">Proyecto</span>
        <span className="post-side-name">{f.nombre}</span>
      </summary>
      <div className="small muted">{TIPO_PROYECTO[f.tipo] || f.tipo}</div>
      <dl className="post-facts">
        {expert && <><dt>Expertos consultados</dt><dd>{f.expertos}</dd></>}
        {expert && <><dt>Causas · efectos</dt><dd>{f.causas.length} · {f.efectos.length}</dd></>}
        <dt>Cálculos terminados</dt><dd>{f.calculos}</dd>
        {f.barridos > 0 && <><dt>Barridos</dt><dd>{f.barridos}</dd></>}
      </dl>
      {expert && f.causas.length > 0 && (
        <>
          <div className="small muted" style={{ marginTop: 10 }}>Causas</div>
          <div className="labels">{f.causas.map((l) => <span key={l} className="pill">{l}</span>)}</div>
          <div className="small muted" style={{ marginTop: 8 }}>Efectos</div>
          <div className="labels">{f.efectos.map((l) => <span key={l} className="pill">{l}</span>)}</div>
        </>
      )}
    </details>
  );
}

function Params({ p }) {
  const items = [];
  if (p.expertos) items.push(['Expertos', p.expertos]);
  if (p.thr?.length) items.push(['Umbral (THR)', p.thr.join(' · ')]);
  if (p.maxorder?.length) items.push(['Orden máximo', p.maxorder.join(' · ')]);
  if (p.reps?.length) items.push(['Repeticiones', p.reps.map(formatCount).join(' · ')]);
  if (!items.length) return null;
  return (
    <details className="post-side-card">
      <summary><span className="post-side-title">Parámetros de los cálculos</span></summary>
      <dl className="post-facts">{items.flatMap(([k, v]) => [<dt key={`${k}-t`}>{k}</dt>, <dd key={`${k}-d`}>{v}</dd>])}</dl>
    </details>
  );
}

const FIG_RE = /\{\{fig:([a-z0-9_-]{1,20})\}\}/g;

/** `{{fig:ref}}` → enlace «Figura N» a la barra lateral. */
function withCitations(markdown, numbers) {
  return markdown.replace(FIG_RE, (_, ref) => (numbers[ref] ? `[Figura ${numbers[ref]}](#figura-${numbers[ref]})` : '**[figura sin publicar]**'));
}

export default function PostView({ post }) {
  const [expanded, setExpanded] = useState(null);
  const [highlight, setHighlight] = useState(null);
  const [open, setOpen] = useState(null); // número de la figura desplegada en la lista
  const [printing, setPrinting] = useState(false);
  const numbers = useMemo(() => Object.fromEntries(post.figuras.map((f) => [f.ref, f.numero])), [post.figuras]);
  const slug = post.publicado_at ? post.slug : null;
  const year = new Date(post.publicado_at || Date.now()).getFullYear();
  const downloadable = post.figuras.filter((f) => f.descargas?.length);
  // Figuras «en el cuerpo»: van bajo la sección donde se citan por primera vez.
  const inline = useMemo(() => {
    const bySection = {};
    for (const f of post.figuras.filter((x) => x.en_cuerpo)) {
      const i = post.bloques.findIndex((b) => b.markdown.includes(`{{fig:${f.ref}}}`));
      if (i >= 0) (bySection[i] ||= []).push(f);
    }
    return bySection;
  }, [post]);
  const inlineRefs = new Set(Object.values(inline).flat().map((f) => f.ref));

  // PDF: se arma la versión para imprimir (todas las figuras, sin la interfaz) y se abre el
  // diálogo de impresión del navegador, donde se elige «Guardar como PDF».
  useEffect(() => {
    if (!printing) return undefined;
    const done = () => setPrinting(false);
    window.addEventListener('afterprint', done);
    const t = setTimeout(() => window.print(), 400); // los gráficos miden su ancho al montarse
    return () => {
      clearTimeout(t);
      window.removeEventListener('afterprint', done);
    };
  }, [printing]);

  // Clic en una cita: despliega esa figura en la lista lateral, la deja a la vista y la resalta.
  function onClick(e) {
    const a = e.target.closest('a[href^="#figura-"]');
    if (!a) return;
    e.preventDefault();
    const n = Number(a.getAttribute('href').slice('#figura-'.length));
    setOpen(n);
    setHighlight(n);
    setTimeout(() => {
      document.getElementById(`figura-${n}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, 0);
    setTimeout(() => setHighlight(null), 1600);
  }

  const markdownLink = ({ href, children, ...rest }) => (
    href?.startsWith('#') ? <a href={href} {...rest}>{children}</a> : <a href={href} target="_blank" rel="noreferrer noopener" {...rest}>{children}</a>
  );

  return (
    <div className="post-layout">
      <article className="post" onClick={onClick}>
        <header className="post-head">
          {post.proyecto && <div className="post-kicker">{TIPO_PROYECTO[post.proyecto.tipo] || post.proyecto.tipo} · {post.proyecto.nombre}</div>}
          <h1 className="post-title">{post.titulo}</h1>
          {post.autores && <div className="post-authors">{post.autores}</div>}
          <div className="post-byline">
            {post.publicado_at ? `Publicado el ${fmtDay(post.publicado_at)}` : 'Vista previa (sin publicar)'}
            <button type="button" className="btn post-pdf no-print" onClick={() => setPrinting(true)} disabled={printing}>
              {printing ? 'Preparando…' : '↓ Descargar PDF'}
            </button>
          </div>
        </header>
        {post.resumen && (
          <section className="post-abstract">
            <h2>Resumen</h2>
            <p>{post.resumen}</p>
            {post.palabras_clave?.length > 0 && <p className="post-keywords"><strong>Palabras clave:</strong> {post.palabras_clave.join('; ')}</p>}
          </section>
        )}
        <div className="prose post-body">
          {post.bloques.map((b, i) => (
            <div key={i} className="post-section">
              {b.markdown.trim() && <Markdown remarkPlugins={[remarkGfm]} components={{ a: markdownLink }}>{withCitations(b.markdown, numbers)}</Markdown>}
              {(inline[i] || []).map((f) => <FigureBlock key={f.ref} b={f} className="post-inline-figure" />)}
            </div>
          ))}
        </div>
        {printing && (
          <section className="post-print-figures" aria-hidden="true">
            {post.figuras.filter((f) => !inlineRefs.has(f.ref)).map((f) => <FigureBlock key={f.ref} b={f} />)}
          </section>
        )}
        {downloadable.length > 0 && (
          <section className="post-availability">
            <h2>Disponibilidad de datos</h2>
            <p>
              Los datos de {downloadable.map((f) => `la Figura ${f.numero}`).join(', ')} se pueden descargar en formato CSV desde la barra lateral.
              {post.ficha?.tipo === 'expertos' && ' Las evaluaciones de los expertos se publican anonimizadas salvo indicación de los autores.'}
            </p>
          </section>
        )}
        <section className="post-cite">
          <strong>Cómo citar:</strong> {post.autores || 'forgethreads'} ({year}). {post.titulo}. <em>Blog forgethreads</em>.
          {slug && <> {window.location.origin}/front/blog/{slug}</>}
        </section>
      </article>
      <aside className="post-side" aria-label="Figuras y datos">
        {post.ficha && <ProjectFacts f={post.ficha} />}
        <Params p={post.parametros} />
        {post.figuras.length > 0 && (
          <section>
            <div className="post-side-title" style={{ marginBottom: 8 }}>Figuras ({post.figuras.length})</div>
            <ol className="post-figlist-public">
              {post.figuras.map((b) => (
                <SideFigure
                  key={b.numero}
                  b={b}
                  slug={slug}
                  open={open === b.numero}
                  highlighted={highlight === b.numero}
                  onToggle={() => setOpen(open === b.numero ? null : b.numero)}
                  onExpand={setExpanded}
                />
              ))}
            </ol>
          </section>
        )}
      </aside>
      {expanded && <ExpandedFigure b={expanded} onClose={() => setExpanded(null)} />}
    </div>
  );
}
