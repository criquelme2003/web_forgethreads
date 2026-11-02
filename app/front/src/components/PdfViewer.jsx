import { useEffect, useLayoutEffect, useRef, useState } from 'react';

/**
 * Muestra un PDF con pdf.js, página por página en canvas, al ancho del contenedor. Así se ve
 * igual en todos los navegadores (también en móviles, que no muestran PDF incrustados).
 * pdf.js se carga recién al abrir un PDF, para no pesar en el resto del SPA, y cada página se
 * dibuja cuando se acerca a la pantalla. Se usa la versión «legacy», que funciona en navegadores
 * más antiguos. El visor tiene alto fijo y su propio scroll, y una barra con zoom (botones,
 * teclas + / − / 0 y Ctrl + rueda o pellizco en el trackpad), la página visible y pantalla completa.
 */

const ZOOM_MIN = 0.5;
const ZOOM_MAX = 4;
const ZOOM_STEP = 1.25;
// Píxeles máximos por canvas: con zoom alto se baja la resolución antes de pasar los límites del
// navegador (Safari no dibuja canvas de más de ~16 millones de píxeles).
const MAX_CANVAS_PIXELS = 16_000_000;

const clampZoom = (z) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(z * 100) / 100));

let pdfjsPromise = null;

function loadPdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = Promise.all([
      import('pdfjs-dist/legacy/build/pdf.min.mjs'),
      import('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url'),
    ]).then(([pdfjs, worker]) => {
      pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
      return pdfjs;
    }).catch((e) => {
      pdfjsPromise = null;
      throw e;
    });
  }
  return pdfjsPromise;
}

function PdfPage({ doc, number, width, ratio, onRatio, root }) {
  const holder = useRef(null);
  const canvas = useRef(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = holder.current;
    if (!el) return undefined;
    if (typeof IntersectionObserver === 'undefined') {
      setVisible(true);
      return undefined;
    }
    // La raíz es la caja con scroll del visor: así el margen adelanta las páginas siguientes.
    const io = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { root, rootMargin: '600px 0px' });
    io.observe(el);
    return () => io.disconnect();
  }, [root]);

  useEffect(() => {
    if (!visible || !width) return undefined;
    let cancelled = false;
    let task = null;
    doc.getPage(number).then((page) => {
      if (cancelled) return;
      const base = page.getViewport({ scale: 1 });
      onRatio(number, base.height / base.width);
      const scale = width / base.width;
      const area = base.width * base.height * scale * scale;
      const dpr = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(MAX_CANVAS_PIXELS / area));
      const viewport = page.getViewport({ scale: scale * dpr });
      const c = canvas.current;
      if (!c) return;
      c.width = Math.floor(viewport.width);
      c.height = Math.floor(viewport.height);
      task = page.render({ canvasContext: c.getContext('2d'), viewport });
      task.promise.catch(() => {}); // cancelada al desmontar o cambiar el ancho
    }).catch(() => {});
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, number, width, visible, onRatio]);

  return (
    <div
      ref={holder}
      className="pdf-page"
      data-page={number}
      style={width ? { width, height: Math.round(width * ratio) } : undefined}
      aria-label={`Página ${number}`}
    >
      <canvas ref={canvas} />
    </div>
  );
}

export default function PdfViewer({ url, title }) {
  const frame = useRef(null);
  const box = useRef(null);
  const [zoom, setZoom] = useState(1); // 1 = ajustado al ancho del visor
  const [fullscreen, setFullscreen] = useState(false);
  const zoomNow = useRef(1);
  const anchor = useRef(null);
  const rememberRef = useRef(() => {}); // punto del documento que se mantiene a la vista al cambiar el zoom
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState('');
  const [width, setWidth] = useState(0);
  const [ratios, setRatios] = useState({}); // página → alto/ancho, cuando ya se leyó
  const [firstRatio, setFirstRatio] = useState(1.414); // A4 hasta leer la primera página
  const [current, setCurrent] = useState(1);

  useEffect(() => {
    let cancelled = false;
    let loaded = null;
    setDoc(null);
    setError('');
    setRatios({});
    setCurrent(1);
    setZoom(1);
    zoomNow.current = 1;
    if (box.current) box.current.scrollTop = 0;
    loadPdfjs()
      .then((pdfjs) => {
        const task = pdfjs.getDocument({ url, withCredentials: true, isEvalSupported: false });
        return task.promise;
      })
      .then(async (d) => {
        loaded = d;
        if (cancelled) {
          d.destroy();
          return;
        }
        const first = (await d.getPage(1)).getViewport({ scale: 1 });
        if (!cancelled) {
          setFirstRatio(first.height / first.width);
          setDoc(d);
        }
      })
      .catch(() => {
        if (!cancelled) setError('No se pudo mostrar el PDF. Puedes descargarlo con el enlace de arriba.');
      });
    return () => {
      cancelled = true;
      loaded?.destroy();
    };
  }, [url]);

  useEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    let t = null;
    // Ancho útil: sin el relleno de la caja (ni la barra de scroll, que clientWidth ya excluye).
    const measure = () => {
      const style = window.getComputedStyle(el);
      const next = Math.max(0, Math.floor(el.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)));
      setWidth((w) => {
        if (w !== next) rememberRef.current(); // el mismo punto sigue a la vista con el nuevo ancho
        return next;
      });
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(() => {
      clearTimeout(t);
      t = setTimeout(measure, 150); // no redibujar en cada paso del cambio de tamaño
    });
    ro.observe(el);
    return () => {
      clearTimeout(t);
      ro.disconnect();
    };
  }, []);

  const onRatio = useRef((number, ratio) => {
    setRatios((r) => (Math.abs((r[number] || 0) - ratio) < 1e-3 ? r : { ...r, [number]: ratio }));
  }).current;

  /** Guarda qué punto del documento está a la vista (el centro, o `at`) para conservarlo. */
  function remember(at) {
    const el = box.current;
    if (!el || anchor.current || !el.scrollHeight) return;
    const rect = el.getBoundingClientRect();
    const x = at ? at.x - rect.left : el.clientWidth / 2;
    const y = at ? at.y - rect.top : el.clientHeight / 2;
    anchor.current = { fx: (el.scrollLeft + x) / el.scrollWidth, fy: (el.scrollTop + y) / el.scrollHeight, x, y };
  }
  rememberRef.current = remember;

  /** Cambia el zoom manteniendo a la vista el mismo punto del documento (el centro, o el cursor). */
  function zoomTo(next, at) {
    const el = box.current;
    // Se parte del último zoom pedido: varios eventos de la rueda pueden llegar antes de redibujar.
    const z = clampZoom(typeof next === 'function' ? next(zoomNow.current) : next);
    if (!el || z === zoomNow.current) return;
    remember(at);
    zoomNow.current = z;
    setZoom(z);
  }

  useLayoutEffect(() => {
    const el = box.current;
    const a = anchor.current;
    if (!el || !a) return;
    anchor.current = null;
    el.scrollLeft = a.fx * el.scrollWidth - a.x;
    el.scrollTop = a.fy * el.scrollHeight - a.y;
  }, [zoom, width, fullscreen]);

  // Ctrl + rueda (o pellizco en el trackpad) hace zoom en el visor y no en toda la página.
  const zoomRef = useRef(zoomTo);
  zoomRef.current = zoomTo;
  useEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const onWheel = (e) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      // Una muesca de la rueda (~100) acerca ×1,28; el pellizco manda deltas pequeños y continuos.
      const delta = Math.max(-50, Math.min(50, e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY));
      const factor = Math.exp(-delta * 0.005);
      zoomRef.current((z) => z * factor, { x: e.clientX, y: e.clientY });
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

  function onKeyDown(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === '+' || e.key === '=') zoomTo((z) => z * ZOOM_STEP);
    else if (e.key === '-') zoomTo((z) => z / ZOOM_STEP);
    else if (e.key === '0') zoomTo(1);
    else return;
    e.preventDefault();
  }

  // Pantalla completa del navegador; donde no existe (iPhone), el visor ocupa toda la ventana.
  const native = typeof document !== 'undefined' && !!document.documentElement.requestFullscreen;
  useEffect(() => {
    const sync = () => setFullscreen(document.fullscreenElement === frame.current);
    document.addEventListener('fullscreenchange', sync);
    return () => document.removeEventListener('fullscreenchange', sync);
  }, []);
  useEffect(() => {
    if (!fullscreen || native) return undefined;
    const onEsc = (e) => e.key === 'Escape' && setFullscreen(false);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', onEsc);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener('keydown', onEsc);
    };
  }, [fullscreen, native]);
  useEffect(() => {
    if (fullscreen) box.current?.focus({ preventScroll: true }); // para usar el teclado de inmediato
  }, [fullscreen]);

  function toggleFullscreen() {
    remember();
    if (!native) {
      setFullscreen((f) => !f);
      return;
    }
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else frame.current?.requestFullscreen().catch(() => setFullscreen(true));
  }

  // Página visible: la última cuyo borde superior ya pasó la mitad de la ventana del visor.
  function onScroll() {
    const el = box.current;
    if (!el) return;
    const middle = el.scrollTop + el.clientHeight / 2;
    let n = 1;
    for (const page of el.querySelectorAll('.pdf-page')) {
      if (page.offsetTop <= middle) n = Number(page.dataset.page);
      else break;
    }
    setCurrent(n);
  }

  const pageWidth = Math.round(width * zoom);
  const expanded = fullscreen && !native ? ' is-expanded' : '';

  return (
    <div className={`pdf-viewer${fullscreen ? ' is-fullscreen' : ''}${expanded}`} ref={frame}>
      <div className="pdf-toolbar" role="toolbar" aria-label="Controles del PDF">
        <div className="pdf-toolbar-group">
          <button type="button" onClick={() => zoomTo((z) => z / ZOOM_STEP)} disabled={!doc || zoom <= ZOOM_MIN} aria-label="Alejar" title="Alejar (−)">−</button>
          <button type="button" className="pdf-zoom" onClick={() => zoomTo(1)} disabled={!doc} title="Ajustar al ancho (0)">
            {Math.round(zoom * 100)} %
          </button>
          <button type="button" onClick={() => zoomTo((z) => z * ZOOM_STEP)} disabled={!doc || zoom >= ZOOM_MAX} aria-label="Acercar" title="Acercar (+)">+</button>
        </div>
        {doc && <span className="small muted" aria-live="polite">Página {current} de {doc.numPages}</span>}
        <button
          type="button"
          onClick={toggleFullscreen}
          disabled={!doc}
          aria-pressed={fullscreen}
          aria-label={fullscreen ? 'Salir de pantalla completa' : 'Pantalla completa'}
          title={fullscreen ? 'Salir de pantalla completa (Esc)' : 'Pantalla completa'}
        >
          {fullscreen ? '✕' : '⛶'}<span className="pdf-btn-label"> {fullscreen ? 'Salir' : 'Pantalla completa'}</span>
        </button>
      </div>
      <div className="pdf-scroll" ref={box} onScroll={onScroll} onKeyDown={onKeyDown} tabIndex={0} role="document" aria-label={title || 'Documento PDF'}>
        {error && <p className="status err small">{error}</p>}
        {!doc && !error && <div className="empty">Cargando el PDF…</div>}
        {doc && (
          <div className="pdf-pages-inner">
            {Array.from({ length: doc.numPages }, (_, i) => (
              <PdfPage
                key={i + 1}
                doc={doc}
                number={i + 1}
                width={pageWidth}
                ratio={ratios[i + 1] || firstRatio}
                onRatio={onRatio}
                root={box.current}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
