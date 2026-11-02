import { useEffect, useRef, useState } from 'react';

/**
 * Muestra un PDF con pdf.js, página por página en canvas, al ancho del contenedor. Así se ve
 * igual en todos los navegadores (también en móviles, que no muestran PDF incrustados).
 * pdf.js se carga recién al abrir un PDF, para no pesar en el resto del SPA, y cada página se
 * dibuja cuando se acerca a la pantalla. Se usa la versión «legacy», que funciona en navegadores
 * más antiguos.
 */

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

function PdfPage({ doc, number, width, ratio, onRatio }) {
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
    const io = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { rootMargin: '600px 0px' });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!visible || !width) return undefined;
    let cancelled = false;
    let task = null;
    doc.getPage(number).then((page) => {
      if (cancelled) return;
      const base = page.getViewport({ scale: 1 });
      onRatio(number, base.height / base.width);
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const viewport = page.getViewport({ scale: (width / base.width) * dpr });
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
    <div ref={holder} className="pdf-page" style={{ height: width ? Math.round(width * ratio) : undefined }} aria-label={`Página ${number}`}>
      <canvas ref={canvas} />
    </div>
  );
}

export default function PdfViewer({ url, title }) {
  const box = useRef(null);
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState('');
  const [width, setWidth] = useState(0);
  const [ratios, setRatios] = useState({}); // página → alto/ancho, cuando ya se leyó
  const [firstRatio, setFirstRatio] = useState(1.414); // A4 hasta leer la primera página

  useEffect(() => {
    let cancelled = false;
    let loaded = null;
    setDoc(null);
    setError('');
    setRatios({});
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
    const measure = () => setWidth(Math.floor(el.clientWidth));
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

  return (
    <div className="pdf-viewer" ref={box} role="document" aria-label={title || 'Documento PDF'}>
      {error && <p className="status err small">{error}</p>}
      {!doc && !error && <div className="empty">Cargando el PDF…</div>}
      {doc && Array.from({ length: doc.numPages }, (_, i) => (
        <PdfPage key={i + 1} doc={doc} number={i + 1} width={width} ratio={ratios[i + 1] || firstRatio} onRatio={onRatio} />
      ))}
      {doc && <div className="small muted pdf-pages">{doc.numPages} {doc.numPages === 1 ? 'página' : 'páginas'}</div>}
    </div>
  );
}
