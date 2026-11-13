// Lightweight SVG charts (no external dependencies).
// Only numbers and pre-formatted dates are interpolated into SVG markup; tooltip text uses textContent.

import { num, shortDate } from "./util.js";

const UP = "#4caf50";
const DOWN = "#df514c";
const GRID = "#eeeeee";
const AXIS = "#9b9b9b";

function niceTicks(min, max, count = 5) {
  const step = (max - min) / count;
  return Array.from({ length: count + 1 }, (_, i) => min + step * i);
}

function frame(container, height) {
  container.innerHTML = "";
  container.classList.add("chart");
  container.style.height = `${height}px`;
  const width = Math.max(300, Math.floor(container.clientWidth || 800));
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.hidden = true;
  return { width, tip };
}

/**
 * Candlestick + volume chart.
 * bars: [{t, o, h, l, c, v}]
 */
export function candleChart(container, bars, { height = 400, label = "Price chart" } = {}) {
  const { width, tip } = frame(container, height);
  if (!bars.length) {
    container.innerHTML = `<p class="muted chart-empty">No price data available.</p>`;
    return;
  }

  const pad = { l: 8, r: 64, t: 12, b: 26 };
  const volH = Math.round(height * 0.16);
  const priceH = height - pad.t - pad.b - volH - 10;
  let min = Math.min(...bars.map((b) => b.l));
  let max = Math.max(...bars.map((b) => b.h));
  const span = max - min || 1;
  min -= span * 0.04;
  max += span * 0.04;
  const vmax = Math.max(...bars.map((b) => b.v)) || 1;

  const n = bars.length;
  const plotW = width - pad.l - pad.r;
  const step = plotW / n;
  const bodyW = Math.max(1, Math.min(11, step * 0.68));
  const x = (i) => pad.l + step * i + step / 2;
  const y = (p) => pad.t + ((max - p) / (max - min)) * priceH;
  const volTop = pad.t + priceH + 10;

  const parts = [];
  for (const t of niceTicks(min, max)) {
    const yy = y(t).toFixed(1);
    parts.push(`<line x1="${pad.l}" x2="${width - pad.r}" y1="${yy}" y2="${yy}" stroke="${GRID}"/>`);
    parts.push(`<text x="${width - pad.r + 6}" y="${yy}" dy="4" class="axis">${num(t)}</text>`);
  }
  const labelEvery = Math.max(1, Math.ceil(n / 6));
  bars.forEach((b, i) => {
    const color = b.c >= b.o ? UP : DOWN;
    const cx = x(i).toFixed(1);
    const top = y(Math.max(b.o, b.c));
    const bh = Math.max(1, Math.abs(y(b.o) - y(b.c)));
    parts.push(`<line x1="${cx}" x2="${cx}" y1="${y(b.h).toFixed(1)}" y2="${y(b.l).toFixed(1)}" stroke="${color}"/>`);
    parts.push(`<rect x="${(x(i) - bodyW / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bodyW.toFixed(1)}" height="${bh.toFixed(1)}" fill="${color}"/>`);
    const vh = (b.v / vmax) * volH;
    parts.push(`<rect x="${(x(i) - bodyW / 2).toFixed(1)}" y="${(volTop + volH - vh).toFixed(1)}" width="${bodyW.toFixed(1)}" height="${vh.toFixed(1)}" fill="${color}" opacity="0.35"/>`);
    if (i % labelEvery === 0) {
      const anchor = i === 0 ? "start" : "middle"; // keep the first label inside the plot
      parts.push(`<text x="${i === 0 ? pad.l : cx}" y="${height - 8}" text-anchor="${anchor}" class="axis">${shortDate(b.t).replace(/, \d{4}$/, "")}</text>`);
    }
  });

  const last = bars[n - 1];
  const ly = y(last.c).toFixed(1);
  const lastColor = last.c >= bars[Math.max(0, n - 2)].c ? UP : DOWN;
  parts.push(`<line x1="${pad.l}" x2="${width - pad.r}" y1="${ly}" y2="${ly}" stroke="${lastColor}" stroke-dasharray="3 3"/>`);
  parts.push(`<rect x="${width - pad.r + 2}" y="${(ly - 9).toFixed(1)}" width="${pad.r - 4}" height="18" rx="2" fill="${lastColor}"/>`);
  parts.push(`<text x="${width - pad.r + 6}" y="${ly}" dy="4" class="axis axis-last">${num(last.c)}</text>`);

  container.insertAdjacentHTML(
    "beforeend",
    `<svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" role="img">
       <title></title>
       ${parts.join("")}
       <line class="xhair" x1="0" x2="0" y1="${pad.t}" y2="${volTop + volH}" stroke="${AXIS}" stroke-dasharray="2 3" visibility="hidden"/>
       <rect class="hit" x="${pad.l}" y="0" width="${plotW}" height="${height}" fill="transparent"/>
     </svg>`
  );
  const svg = container.querySelector("svg");
  svg.querySelector("title").textContent = label;
  container.appendChild(tip);

  const xhair = svg.querySelector(".xhair");
  const hit = svg.querySelector(".hit");
  hit.addEventListener("mousemove", (e) => {
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    const i = Math.min(n - 1, Math.max(0, Math.floor((px - pad.l) / step)));
    const b = bars[i];
    xhair.setAttribute("x1", x(i));
    xhair.setAttribute("x2", x(i));
    xhair.setAttribute("visibility", "visible");
    tip.hidden = false;
    tip.textContent = `${shortDate(b.t)}  O ${num(b.o)}  H ${num(b.h)}  L ${num(b.l)}  C ${num(b.c)}  Vol ${Math.round(b.v).toLocaleString("en-US")}`;
  });
  hit.addEventListener("mouseleave", () => {
    xhair.setAttribute("visibility", "hidden");
    tip.hidden = true;
  });
}

/**
 * Area/line chart. points: [{t, v}]
 */
export function lineChart(container, points, { height = 220, label = "Line chart", prefix = "" } = {}) {
  const { width, tip } = frame(container, height);
  if (points.length < 2) {
    container.innerHTML = `<p class="muted chart-empty">Not enough data to chart.</p>`;
    return;
  }
  const pad = { l: 8, r: 70, t: 10, b: 24 };
  const vals = points.map((p) => p.v);
  let min = Math.min(...vals);
  let max = Math.max(...vals);
  const span = max - min || Math.abs(max) * 0.01 || 1;
  min -= span * 0.08;
  max += span * 0.08;
  const n = points.length;
  const plotW = width - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;
  const x = (i) => pad.l + (plotW * i) / (n - 1);
  const y = (v) => pad.t + ((max - v) / (max - min)) * plotH;
  const color = vals[n - 1] >= vals[0] ? UP : DOWN;
  const gid = `g${Math.random().toString(36).slice(2, 8)}`;

  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
  const area = `${path}L${x(n - 1).toFixed(1)},${pad.t + plotH}L${pad.l},${pad.t + plotH}Z`;
  const parts = [];
  for (const t of niceTicks(min, max, 4)) {
    const yy = y(t).toFixed(1);
    parts.push(`<line x1="${pad.l}" x2="${width - pad.r}" y1="${yy}" y2="${yy}" stroke="${GRID}"/>`);
    parts.push(`<text x="${width - pad.r + 6}" y="${yy}" dy="4" class="axis">${prefix}${num(t, t >= 1000 ? 0 : 2)}</text>`);
  }
  const labelEvery = Math.max(1, Math.ceil(n / 5));
  points.forEach((p, i) => {
    if (i % labelEvery === 0) {
      const anchor = i === 0 ? "start" : "middle"; // keep the first label inside the plot
      parts.push(`<text x="${x(i).toFixed(1)}" y="${height - 6}" text-anchor="${anchor}" class="axis">${shortDate(p.t).replace(/, \d{4}$/, "")}</text>`);
    }
  });

  container.insertAdjacentHTML(
    "beforeend",
    `<svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" role="img">
       <title></title>
       <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
         <stop offset="0" stop-color="${color}" stop-opacity="0.22"/><stop offset="1" stop-color="${color}" stop-opacity="0"/>
       </linearGradient></defs>
       ${parts.join("")}
       <path d="${area}" fill="url(#${gid})"/>
       <path d="${path}" fill="none" stroke="${color}" stroke-width="1.6"/>
       <circle class="dot" r="3.5" fill="${color}" visibility="hidden"/>
       <rect class="hit" x="${pad.l}" y="0" width="${plotW}" height="${height}" fill="transparent"/>
     </svg>`
  );
  const svg = container.querySelector("svg");
  svg.querySelector("title").textContent = label;
  container.appendChild(tip);

  const dot = svg.querySelector(".dot");
  svg.querySelector(".hit").addEventListener("mousemove", (e) => {
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    const i = Math.min(n - 1, Math.max(0, Math.round(((px - pad.l) / plotW) * (n - 1))));
    dot.setAttribute("cx", x(i));
    dot.setAttribute("cy", y(points[i].v));
    dot.setAttribute("visibility", "visible");
    tip.hidden = false;
    tip.textContent = `${shortDate(points[i].t)}  ${prefix}${num(points[i].v)}`;
  });
  svg.querySelector(".hit").addEventListener("mouseleave", () => {
    dot.setAttribute("visibility", "hidden");
    tip.hidden = true;
  });
}
