// App shell: routing, header, polling and shared context.

import { api } from "./api.js";
import { createMarketwatch } from "./marketwatch.js";
import { arrow, esc, num, pct, SOURCE_LABELS, toast, tone } from "./util.js";
import { views } from "./views.js";

const POLL_MS = 5000;
const main = document.getElementById("main");
const banner = document.getElementById("banner");

const ctx = {
  quotes: new Map(), // symbol -> latest quote (shared by marketwatch, order window, charts)
  cash: null,
  navigate: (hash) => {
    if (location.hash === hash) route();
    else location.hash = hash;
  },
  refresh: () => tick(),
  watchlist: () => marketwatch.symbols(),
};

const marketwatch = createMarketwatch(document.getElementById("marketwatch"), ctx);
let current = null; // { name, instance }
let failures = 0;

/* ---------------- header ---------------- */

async function updateHeader() {
  const { indices } = await api.indices();
  document.getElementById("indices").innerHTML = indices
    .map((i) => {
      ctx.quotes.set(i.symbol, i);
      return `<a class="index ${tone(i.change)}" href="#/chart/${esc(i.symbol)}" title="${esc(i.symbol)}">
        <span class="index-name">${esc(i.label)}</span>
        <span class="index-ltp">${esc(num(i.price))}</span>
        <span class="index-chg">${arrow(i.change)} ${esc(pct(i.change_pct))}</span></a>`;
    })
    .join("");
  const src = indices[0] && indices[0].source;
  const badge = document.getElementById("source-badge");
  badge.textContent = SOURCE_LABELS[src] || src || "—";
  badge.className = `badge ${src === "simulated" ? "badge-warn" : "badge-ok"}`;
  badge.title = src === "simulated"
    ? "No Alpaca keys configured (or Alpaca unavailable). Prices are simulated."
    : "Live prices from Alpaca's IEX feed";
}

async function updateEngineStatus() {
  const s = await api.riskStatus();
  const mode = document.getElementById("mode-badge");
  mode.textContent = String(s.trading_mode).toUpperCase();
  const dot = document.getElementById("engine-dot");
  dot.className = `status-dot ${s.trading_halted ? "halted" : "ok"}`;
  dot.setAttribute("aria-label", s.trading_halted ? "Trading halted" : "Engine online, trading active");
  dot.title = s.trading_halted ? `Trading halted: ${s.halt_reason || ""}` : "Engine online";
}

/* ---------------- routing ---------------- */

function parseHash() {
  const [, name = "dashboard", ...params] = (location.hash || "#/dashboard").split("/");
  return { name: views[name] ? name : "dashboard", params: params.map(decodeURIComponent) };
}

async function route() {
  const { name, params } = parseHash();
  if (current && current.instance && current.instance.destroy) current.instance.destroy();
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === name) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  document.title = `${views[name].title}${params[0] ? ` · ${params[0]}` : ""} · Trading Engine`;
  closeWatchlistOnMobile();

  main.innerHTML = `<p class="muted">Loading…</p>`;
  const token = {};
  current = { name, instance: null, token };
  try {
    const instance = await views[name].mount(main, ctx, params);
    if (current.token !== token) return; // navigated away while mounting
    current.instance = instance || {};
    if (current.instance.update) await current.instance.update();
  } catch (err) {
    main.innerHTML = `<section class="card"><p class="error-text" role="alert">${esc(err.message || "Failed to load")}</p></section>`;
  }
  main.focus({ preventScroll: true });
}

/* ---------------- polling ---------------- */

let ticking = false;
async function tick() {
  if (ticking) return;
  ticking = true;
  try {
    const jobs = [updateHeader(), updateEngineStatus(), marketwatch.update()];
    if (current && current.instance && current.instance.update) jobs.push(current.instance.update());
    const results = await Promise.allSettled(jobs);
    const failed = results.filter((r) => r.status === "rejected");
    if (failed.length === results.length) throw failed[0].reason;
    failures = 0;
    banner.hidden = true;
  } catch (err) {
    failures += 1;
    if (err && err.status === 0) {
      banner.hidden = false;
      banner.textContent = failures > 1
        ? "Can't reach the trading engine. Retrying… (free Render instances can take ~30s to wake up)"
        : "Connecting to the trading engine…";
    }
  } finally {
    ticking = false;
  }
}

setInterval(() => {
  if (!document.hidden && !document.getElementById("modal-root").children.length) tick();
}, POLL_MS);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) tick();
});

/* ---------------- mobile watchlist toggle ---------------- */

const toggle = document.getElementById("watch-toggle");
const mw = document.getElementById("marketwatch");
toggle.addEventListener("click", () => {
  const open = !mw.classList.contains("open");
  mw.classList.toggle("open", open);
  toggle.setAttribute("aria-expanded", String(open));
});
function closeWatchlistOnMobile() {
  mw.classList.remove("open");
  toggle.setAttribute("aria-expanded", "false");
}

/* ---------------- boot ---------------- */

window.addEventListener("hashchange", route);
window.addEventListener("unhandledrejection", (e) => {
  if (e.reason && e.reason.status && e.reason.status !== 0) {
    toast(e.reason.message, { kind: "error" });
  }
});

tick().then(route);
