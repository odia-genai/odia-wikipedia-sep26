// Tiny SVG charts: a histogram (one series, one hue) and a horizontal bar list.
// Marks carry the series colour; every value is also in the tooltip and the stats line.
import { h, num, compact, tipOn } from "./util.js";

const SVGNS = "http://www.w3.org/2000/svg";
function s(tag, attrs) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  return el;
}

function niceTicks(max, n = 3) {
  if (max <= 0) return [0];
  const step0 = max / n;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= step0) || step0;
  const out = [];
  for (let v = 0; v <= max + 1e-9; v += step) out.push(v);
  return out;
}

function fmtEdge(x, integer) {
  if (integer) return num(Math.round(x));
  const a = Math.abs(x);
  if (a >= 1000) return compact(x);
  if (a >= 10) return x.toFixed(1);
  if (a >= 1) return x.toFixed(2);
  return x.toFixed(3);
}

// dist: {edges, counts, log, integer, ...}; onPick(lo, hi) when a bar is clicked.
export function histogram(dist, { width = 380, height = 120, onPick } = {}) {
  const m = { l: 36, r: 6, t: 6, b: 20 };
  const W = width - m.l - m.r, H = height - m.t - m.b;
  const counts = dist.counts, edges = dist.edges;
  const max = Math.max(...counts, 1);
  const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": `histogram, ${counts.length} bins` });
  const g = s("g", { transform: `translate(${m.l},${m.t})` });
  svg.append(g);
  const ticks = niceTicks(max);
  const grid = s("g", { class: "grid" });
  for (const t of ticks) {
    const y = H - (t / max) * H;
    grid.append(s("line", { x1: 0, x2: W, y1: y, y2: y }));
    const lab = s("text", { x: -5, y: y + 3, "text-anchor": "end" });
    lab.textContent = compact(t);
    g.append(lab);
  }
  g.append(grid);
  const n = counts.length;
  const bw = W / n;
  const gap = n > 1 ? Math.min(2, bw * 0.25) : 0;
  counts.forEach((c, i) => {
    const x = i * bw, bh = (c / max) * H;
    const lo = edges[i], hi = edges[i + 1];
    const label = dist.integer && hi - lo === 1 ? `${fmtEdge(lo, true)}` : `${fmtEdge(lo, dist.integer)} – ${fmtEdge(hi, dist.integer)}`;
    const hit = s("rect", { class: "hit", x, y: 0, width: bw, height: H, tabindex: 0 });
    const w = Math.max(bw - gap, 0.5);
    const r = Math.min(3, w / 2, bh);
    // bar with rounded top corners, square at the baseline
    const path = bh > 0 ? `M${x},${H} V${H - bh + r} Q${x},${H - bh} ${x + r},${H - bh} H${x + w - r} Q${x + w},${H - bh} ${x + w},${H - bh + r} V${H} Z` : "";
    const bar = s("path", { class: "bar", d: path });
    tipOn(hit, () => h("div", null, h("strong", null, num(c)), " articles", h("br"), h("span", { class: "muted" }, label)));
    if (onPick) {
      hit.addEventListener("click", () => onPick(lo, hi, i === n - 1));
      hit.addEventListener("keydown", (e) => { if (e.key === "Enter") onPick(lo, hi, i === n - 1); });
    }
    g.append(hit, bar);
  });
  const axis = s("g", { class: "axis" });
  axis.append(s("line", { x1: 0, x2: W, y1: H, y2: H }));
  g.append(axis);
  const xt = dist.log ? 4 : 5;
  for (let k = 0; k <= xt; k++) {
    const i = Math.round((k / xt) * n);
    const x = i * bw;
    const lab = s("text", { x, y: H + 13, "text-anchor": k === 0 ? "start" : k === xt ? "end" : "middle" });
    lab.textContent = fmtEdge(edges[Math.min(i, edges.length - 1)], dist.integer);
    g.append(lab);
  }
  return h("div", { class: "chart" }, svg);
}

// options: [[value, count], ...]; onPick(value)
export function barList(options, { onPick, total, limit = 20, label = (v) => v } = {}) {
  const shown = options.slice(0, limit);
  const max = Math.max(...shown.map((o) => o[1]), 1);
  const el = h("div", { class: "barlist" });
  for (const [v, c] of shown) {
    const text = v === null || v === "__null__" ? "(missing)" : label(v);
    const lbl = onPick ? h("a", { class: "lbl", href: "javascript:void 0", title: String(text), onclick: (e) => { e.preventDefault(); onPick(v); } }, text)
      : h("span", { class: "lbl", title: String(text) }, text);
    const fill = h("div", { class: "fill", style: { width: `${(100 * c) / max}%` } });
    tipOn(fill, () => h("div", null, h("strong", null, num(c)), total ? ` (${((100 * c) / total).toFixed(1)}%)` : "", h("br"), String(text)));
    el.append(lbl, h("div", { class: "track" }, fill), h("span", { class: "n" }, num(c)));
  }
  if (options.length > limit) el.append(h("span", { class: "muted small" }, `+ ${options.length - limit} more`), h("span"), h("span"));
  return el;
}

export function progressBar(parts, total) {
  const bar = h("div", { class: "progress", role: "img", "aria-label": parts.map((p) => `${p.label} ${p.n}`).join(", ") });
  for (const p of parts) {
    if (!p.n) continue;
    const seg = h("span", { class: p.cls, style: { width: `${(100 * p.n) / Math.max(total, 1)}%` } });
    tipOn(seg, () => h("div", null, h("strong", null, num(p.n)), " ", p.label));
    bar.append(seg);
  }
  return bar;
}
