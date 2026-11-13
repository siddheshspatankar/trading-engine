// Marketwatch sidebar (watchlist with live quotes and quick-trade actions).

import { api } from "./api.js";
import { arrow, esc, num, pct, signed, toast, tone } from "./util.js";
import { openOrderWindow } from "./order-window.js";

const STORAGE_KEY = "te.watchlist.v1";
const MAX_ITEMS = 50;
const DEFAULT_LIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "JPM", "V"];

function load() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (Array.isArray(saved) && saved.every((s) => typeof s === "string")) return saved.slice(0, MAX_ITEMS);
  } catch {
    /* ignore corrupt storage */
  }
  return [...DEFAULT_LIST];
}

export function createMarketwatch(root, ctx) {
  let symbols = load();
  const save = () => localStorage.setItem(STORAGE_KEY, JSON.stringify(symbols));

  root.innerHTML = `
    <form class="mw-search" role="search">
      <label for="mw-input" class="sr-only">Add a symbol to the watchlist</label>
      <input id="mw-input" type="search" autocomplete="off" spellcheck="false" maxlength="10"
             placeholder="Search & add, e.g. AMD, NFLX" />
      <span class="mw-count" id="mw-count" aria-live="polite"></span>
    </form>
    <ul class="mw-list" id="mw-list"></ul>
    <p class="mw-foot muted">Hover a row (or focus it and press <kbd>B</kbd> / <kbd>S</kbd>) to trade.</p>`;

  const list = root.querySelector("#mw-list");
  const input = root.querySelector("#mw-input");

  function rebuild() {
    root.querySelector("#mw-count").textContent = `${symbols.length} / ${MAX_ITEMS}`;
    if (!symbols.length) {
      list.innerHTML = `<li class="mw-empty muted">Your watchlist is empty. Add a symbol above.</li>`;
      return;
    }
    list.innerHTML = symbols
      .map(
        (s) => `
      <li class="mw-row" data-symbol="${esc(s)}" tabindex="0" aria-label="${esc(s)}">
        <span class="mw-sym">${esc(s)}</span>
        <span class="mw-chg" data-f="chg"></span>
        <span class="mw-pct" data-f="pct"></span>
        <span class="mw-arrow" data-f="arrow" aria-hidden="true"></span>
        <span class="mw-ltp" data-f="ltp">—</span>
        <span class="mw-actions">
          <button type="button" class="mw-btn mw-buy" data-act="buy" aria-label="Buy ${esc(s)}">B</button>
          <button type="button" class="mw-btn mw-sell" data-act="sell" aria-label="Sell ${esc(s)}">S</button>
          <button type="button" class="mw-btn" data-act="chart" aria-label="Chart ${esc(s)}">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2 13h12M4 10l3-3 2 2 4-5" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>
          </button>
          <button type="button" class="mw-btn" data-act="remove" aria-label="Remove ${esc(s)} from watchlist">×</button>
        </span>
      </li>`
      )
      .join("");
    paint();
  }

  function paint() {
    for (const row of list.querySelectorAll(".mw-row")) {
      const q = ctx.quotes.get(row.dataset.symbol);
      if (!q) continue;
      const t = tone(q.change_pct);
      const ltpEl = row.querySelector('[data-f="ltp"]');
      const prev = Number(ltpEl.dataset.v);
      if (prev && prev !== q.price) {
        ltpEl.classList.remove("tick-up", "tick-down");
        void ltpEl.offsetWidth; // restart animation
        ltpEl.classList.add(q.price > prev ? "tick-up" : "tick-down");
      }
      ltpEl.dataset.v = q.price;
      ltpEl.textContent = num(q.price);
      row.querySelector('[data-f="chg"]').textContent = signed(q.change);
      row.querySelector('[data-f="pct"]').textContent = pct(q.change_pct);
      row.querySelector('[data-f="arrow"]').textContent = arrow(q.change_pct);
      row.className = `mw-row ${t}`;
      row.setAttribute("aria-label", `${q.symbol} ${num(q.price)}, ${pct(q.change_pct)} today`);
    }
  }

  async function update() {
    if (!symbols.length) return;
    const { quotes } = await api.quotes(symbols);
    for (const q of quotes) ctx.quotes.set(q.symbol, q);
    paint();
  }

  function act(action, symbol) {
    if (action === "buy" || action === "sell") openOrderWindow(ctx, { symbol, side: action });
    else if (action === "chart") ctx.navigate(`#/chart/${encodeURIComponent(symbol)}`);
    else if (action === "remove") {
      symbols = symbols.filter((s) => s !== symbol);
      save();
      rebuild();
    }
  }

  list.addEventListener("click", (e) => {
    const row = e.target.closest(".mw-row");
    if (!row) return;
    const btn = e.target.closest("[data-act]");
    act(btn ? btn.dataset.act : "chart", row.dataset.symbol);
  });

  list.addEventListener("keydown", (e) => {
    const row = e.target.closest(".mw-row");
    if (!row || e.target !== row) return;
    const key = e.key.toLowerCase();
    if (key === "b") act("buy", row.dataset.symbol);
    else if (key === "s") act("sell", row.dataset.symbol);
    else if (key === "enter") act("chart", row.dataset.symbol);
    else if (key === "delete" || key === "backspace") act("remove", row.dataset.symbol);
    else if (key === "arrowdown" || key === "arrowup") {
      e.preventDefault();
      const sib = key === "arrowdown" ? row.nextElementSibling : row.previousElementSibling;
      if (sib) sib.focus();
    } else return;
    e.preventDefault();
  });

  root.querySelector(".mw-search").addEventListener("submit", async (e) => {
    e.preventDefault();
    const sym = input.value.trim().toUpperCase();
    if (!sym) return;
    if (symbols.includes(sym)) {
      toast(`${sym} is already in your watchlist.`);
      return;
    }
    if (symbols.length >= MAX_ITEMS) {
      toast(`Watchlist is full (${MAX_ITEMS} symbols).`, { kind: "warn" });
      return;
    }
    try {
      const { quotes } = await api.quotes([sym]);
      ctx.quotes.set(quotes[0].symbol, quotes[0]);
      symbols.push(quotes[0].symbol);
      save();
      input.value = "";
      rebuild();
    } catch (err) {
      toast(err.message, { kind: "error", title: `Couldn't add ${sym}` });
    }
  });

  rebuild();
  return { update, symbols: () => [...symbols] };
}
