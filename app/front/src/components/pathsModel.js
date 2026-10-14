import { useEffect, useState } from 'react';

/**
 * Modelo común de los caminos (efectos olvidados) para el chord y el hive plot.
 *
 * Color por orden: rampa ordinal de un solo tono (azul claro → oscuro, más indirecto = más
 * oscuro), validada con el validador de la guía de visualización (--ordinal, superficie clara).
 * Solo 5 pasos aprueban la separación mínima entre vecinos. Con hasta 5 órdenes, cada uno
 * toma un paso repartido a lo largo de la rampa (con 3 órdenes: claro, medio y oscuro); con
 * más, los órdenes 6 o más comparten el último paso ("6+").
 */

export const ORDER_RAMP = ['#86b6ef', '#5598e7', '#2a78d6', '#1c5cab', '#104281'];
export const FIRST_ORDER = 2;

/**
 * Paso de la rampa para el elemento `i` de `count` (0-based). Con hasta 5 elementos se reparten a
 * lo largo de toda la rampa (más separación); con más, desde el quinto comparten el más oscuro.
 */
export function rampColor(i, count) {
  const n = ORDER_RAMP.length;
  if (count <= 1) return ORDER_RAMP[2];
  if (count <= n) return ORDER_RAMP[Math.round((i * (n - 1)) / (count - 1))];
  return ORDER_RAMP[Math.max(0, Math.min(i, n - 1))];
}

/** `orders`: todos los órdenes del job, para repartir los pasos de la rampa. */
export function orderColor(order, orders) {
  if (orders && orders.length <= ORDER_RAMP.length) {
    const sorted = [...orders].sort((a, b) => a - b);
    const i = sorted.indexOf(order);
    if (i >= 0) return rampColor(i, sorted.length);
  }
  return ORDER_RAMP[Math.max(0, Math.min(order - FIRST_ORDER, ORDER_RAMP.length - 1))];
}

/** Cantidad de tramos de un camino (= su orden): origen → intermedios → destino. */
export function hopCount(p) {
  return p.through.length + 1;
}

export function maxHops(paths) {
  return paths.reduce((m, p) => Math.max(m, hopCount(p)), 0);
}

/**
 * Color del tramo `hop` (0 = el que sale del origen) de un camino de `hops` tramos: el primero
 * es siempre el más claro y el último el más oscuro, sea cual sea el largo del camino.
 */
export function hopColor(hop, hops) {
  if (hops <= 1) return ORDER_RAMP[ORDER_RAMP.length - 1];
  return ORDER_RAMP[Math.round((hop * (ORDER_RAMP.length - 1)) / (hops - 1))];
}

/** Clave de leyenda: con más de 5 órdenes, desde el 6 comparten color y se agrupan en "6+". */
export function orderBucket(order, orders) {
  if (orders && orders.length <= ORDER_RAMP.length) return String(order);
  const last = FIRST_ORDER + ORDER_RAMP.length - 1;
  return order >= last + 1 ? `${last + 1}+` : String(order);
}

export function fmtValue(v) {
  return Number.isInteger(v) ? String(v) : Number(v).toFixed(4);
}

/**
 * Convierte las tablas por orden en una lista de caminos.
 * Cada fila es From, Through_1..k, To, [Count], Mean, [SD].
 */
export function tablesToPaths(tables, metric) {
  const paths = [];
  for (const table of tables) {
    const { columns, rows, order } = table;
    const fromIdx = columns.indexOf('From');
    const toIdx = columns.indexOf('To');
    const throughIdx = columns.map((c, i) => (c.startsWith('Through') ? i : -1)).filter((i) => i >= 0);
    const metricIdx = columns.indexOf(metric);
    const meanIdx = columns.indexOf('Mean');
    const countIdx = columns.indexOf('Count');
    rows.forEach((row, i) => {
      const value = Number(row[metricIdx]);
      if (!(value > 0)) return;
      paths.push({
        id: `${order}:${i}`,
        order,
        from: String(row[fromIdx]),
        through: throughIdx.map((j) => String(row[j])),
        to: String(row[toIdx]),
        value,
        mean: meanIdx >= 0 ? Number(row[meanIdx]) : null,
        count: countIdx >= 0 ? Number(row[countIdx]) : null,
      });
    });
  }
  return paths;
}

/** Filtra por origen y órdenes visibles, y deja los `top` de mayor valor por orden (0 = todos). */
export function selectPaths(paths, { origin, orders, top }) {
  const byOrder = new Map();
  for (const p of paths) {
    if (origin && p.from !== origin) continue;
    if (orders && !orders.has(p.order)) continue;
    if (!byOrder.has(p.order)) byOrder.set(p.order, []);
    byOrder.get(p.order).push(p);
  }
  const out = [];
  for (const list of byOrder.values()) {
    list.sort((a, b) => b.value - a.value);
    out.push(...(top ? list.slice(0, top) : list));
  }
  return out;
}

/**
 * Etiquetas de `used` en el orden de `preferred` (las del proyecto) y, al final, las que no
 * están en esa lista, ordenadas. Así ningún nodo de un camino queda sin posición aunque su
 * etiqueta no coincida exactamente con las del proyecto.
 */
export function orderLabels(used, preferred) {
  const known = preferred.filter((l) => used.has(l));
  const knownSet = new Set(known);
  const rest = [...used].filter((l) => !knownSet.has(l)).sort();
  return [...known, ...rest];
}

export function maxValue(paths) {
  return paths.reduce((m, p) => (p.value > m ? p.value : m), 0);
}

export function pathLabel(p) {
  return [p.from, ...p.through, p.to].join(' → ');
}

/** Ancho del contenedor (ResizeObserver). */
export function useWidth(ref) {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return undefined;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, [ref]);
  return width;
}
