// Page views. Each view: mount(el, ctx, params) -> { update?(): Promise, destroy?() }
// mount() renders the static layout once; update() refreshes data in place (safe to poll).

import { api } from "./api.js";
import { candleChart, lineChart } from "./chart.js";
import { openOrderWindow } from "./order-window.js";
import {
  arrow, clock, esc, num, pct, qty, setBusy, shortDate, signed, signedUsd,
  SOURCE_LABELS, toast, tone, usd,
} from "./util.js";

const $ = (root, sel) => root.querySelector(sel);

const statusBadge = (s) => {
  const map = {
    filled: ["Complete", "ok"], partially_filled: ["Partial", "warn"], accepted: ["Open", "info"],
    pending: ["Pending", "info"], submitted: ["Open", "info"], cancelled: ["Cancelled", "muted"],
    rejected: ["Rejected", "bad"], expired: ["Expired", "muted"],
  };
  const [label, kind] = map[s] || [s, "muted"];
  return `<span class="pill pill-${kind}">${esc(label)}</span>`;
};
const sideBadge = (side) =>
  `<span class="side side-${side === "buy" ? "buy" : "sell"}">${side === "buy" ? "BUY" : "SELL"}</span>`;
const riskBadge = (d) =>
  d ? `<span class="pill pill-${d === "approved" ? "ok" : d === "modified" ? "warn" : "bad"}">${esc(d)}</span>` : "—";

function emptyRow(cols, text) {
  return `<tr><td colspan="${cols}" class="empty muted">${esc(text)}</td></tr>`;
}

/* ------------------------------------------------------------------ Dashboard */

const dashboard = {
  title: "Dashboard",
  mount(el, ctx) {
    el.innerHTML = `
      <h1 class="greeting">Hi, Trader</h1>
      <div class="grid-2">
        <section class="card" aria-labelledby="d-eq-h">
          <h2 id="d-eq-h" class="card-title">Equity</h2>
          <div class="kpi"><span class="kpi-value" id="d-cash">—</span><span class="kpi-label">Cash available</span></div>
          <dl class="kv">
            <div><dt>Invested</dt><dd id="d-invested">—</dd></div>
            <div><dt>Account value</dt><dd id="d-equity">—</dd></div>
            <div><dt>Opening balance</dt><dd id="d-opening">—</dd></div>
          </dl>
          <a class="link" href="#/funds">View funds</a>
        </section>
        <section class="card" aria-labelledby="d-pos-h">
          <h2 id="d-pos-h" class="card-title">Positions <span id="d-npos" class="muted"></span></h2>
          <div class="kpi"><span class="kpi-value" id="d-pnl">—</span><span class="kpi-label">Unrealised P&amp;L <span id="d-pnlpct"></span></span></div>
          <div class="bar" aria-hidden="true"><span id="d-bar"></span></div>
          <dl class="kv">
            <div><dt>Current value</dt><dd id="d-current">—</dd></div>
            <div><dt>Total P&amp;L since start</dt><dd id="d-total">—</dd></div>
            <div><dt>Exposure</dt><dd id="d-exposure">—</dd></div>
          </dl>
          <a class="link" href="#/positions">View positions</a>
        </section>
      </div>

      <section class="card" aria-labelledby="d-mkt-h">
        <div class="card-head">
          <h2 id="d-mkt-h" class="card-title">Market overview <span class="muted">S&amp;P 500 (SPY), 6 months</span></h2>
          <span class="badge" id="d-mkt-src"></span>
        </div>
        <div id="d-chart"></div>
      </section>

      <div class="grid-2">
        <section class="card" aria-labelledby="d-eng-h">
          <h2 id="d-eng-h" class="card-title">Engine</h2>
          <dl class="kv" id="d-engine"></dl>
          <a class="link" href="#/risk">Risk controls</a>
        </section>
        <section class="card" aria-labelledby="d-log-h">
          <h2 id="d-log-h" class="card-title">Recent risk decisions</h2>
          <ul class="feed" id="d-feed"></ul>
        </section>
      </div>`;

    api.bars("SPY", 180).then((r) => {
      lineChart($(el, "#d-chart"), r.bars.map((b) => ({ t: b.t, v: b.c })), { height: 230, label: "SPY closing prices, last 6 months", prefix: "$" });
      $(el, "#d-mkt-src").textContent = SOURCE_LABELS[r.source] || r.source;
    }).catch(() => ($(el, "#d-chart").innerHTML = `<p class="muted">Chart unavailable.</p>`));

    return {
      async update() {
        const [p, risk, log] = await Promise.all([api.portfolio(), api.riskStatus(), api.riskLog(6)]);
        ctx.cash = p.cash;
        $(el, "#d-cash").textContent = usd(p.cash);
        $(el, "#d-invested").textContent = usd(p.invested);
        $(el, "#d-equity").textContent = usd(p.equity);
        $(el, "#d-opening").textContent = usd(p.initial_capital);
        $(el, "#d-npos").textContent = `(${p.num_positions})`;
        const pnl = $(el, "#d-pnl");
        pnl.textContent = signedUsd(p.unrealized_pnl);
        pnl.className = `kpi-value ${tone(p.unrealized_pnl)}`;
        $(el, "#d-pnlpct").innerHTML = `<span class="${tone(p.unrealized_pnl_pct)}">${esc(pct(p.unrealized_pnl_pct))}</span>`;
        $(el, "#d-current").textContent = usd(p.current_value);
        $(el, "#d-total").innerHTML = `<span class="${tone(p.total_pnl)}">${esc(signedUsd(p.total_pnl))} (${esc(pct(p.total_pnl_pct))})</span>`;
        $(el, "#d-exposure").textContent = `${num(p.exposure_pct)}% of equity`;
        $(el, "#d-bar").style.width = `${Math.min(100, p.exposure_pct)}%`;

        $(el, "#d-engine").innerHTML = `
          <div><dt>Trading</dt><dd>${risk.trading_halted ? `<span class="pill pill-bad">Halted</span>` : `<span class="pill pill-ok">Active</span>`}</dd></div>
          <div><dt>Mode</dt><dd>${esc(risk.trading_mode)}</dd></div>
          <div><dt>Max position</dt><dd>${num(risk.limits.max_position_pct * 100, 0)}% of equity</dd></div>
          <div><dt>Daily loss halt</dt><dd>${num(risk.limits.max_daily_loss_pct * 100, 0)}%</dd></div>
          <div><dt>Commission paid</dt><dd>${esc(usd(p.commission_paid))}</dd></div>`;

        $(el, "#d-feed").innerHTML = log.entries.length
          ? log.entries.map((e) => `
              <li><span class="muted">${esc(clock(e.timestamp))}</span>
                ${sideBadge(e.side)} <strong>${esc(e.symbol)}</strong> ${esc(qty(e.requested_qty))}
                ${riskBadge(e.decision)}
                ${e.reason ? `<div class="muted small">${esc(e.reason)}</div>` : ""}</li>`).join("")
          : `<li class="muted">No orders yet. Pick a symbol in Marketwatch and press B to buy.</li>`;
      },
    };
  },
};

/* ------------------------------------------------------------------ Chart */

const RANGES = [["1M", 30], ["3M", 90], ["6M", 180], ["1Y", 365], ["2Y", 730]];

const chart = {
  title: "Chart",
  mount(el, ctx, [symbolParam]) {
    const symbol = (symbolParam || "SPY").toUpperCase();
    let days = 180;
    el.innerHTML = `
      <section class="card">
        <div class="chart-head">
          <div>
            <h1 class="chart-sym">${esc(symbol)}</h1>
            <div class="chart-quote" id="c-quote">Loading…</div>
          </div>
          <div class="chart-actions">
            <button class="btn btn-buy" id="c-buy" type="button">Buy</button>
            <button class="btn btn-sell" id="c-sell" type="button">Sell</button>
          </div>
        </div>
        <dl class="ohlc" id="c-ohlc"></dl>
        <div class="chart-toolbar">
          <div class="seg" role="group" aria-label="Chart range">
            ${RANGES.map(([l, d]) => `<button type="button" data-days="${d}" aria-pressed="${d === days}">${l}</button>`).join("")}
          </div>
          <span class="badge" id="c-src"></span>
        </div>
        <div id="c-chart"></div>
      </section>`;

    $(el, "#c-buy").addEventListener("click", () => openOrderWindow(ctx, { symbol, side: "buy" }));
    $(el, "#c-sell").addEventListener("click", () => openOrderWindow(ctx, { symbol, side: "sell" }));

    async function draw() {
      const box = $(el, "#c-chart");
      try {
        const r = await api.bars(symbol, days);
        candleChart(box, r.bars, { height: 420, label: `${symbol} daily candlestick chart, ${r.bars.length} sessions` });
        $(el, "#c-src").textContent = SOURCE_LABELS[r.source] || r.source;
      } catch (err) {
        box.innerHTML = `<p class="muted">${esc(err.message)}</p>`;
      }
    }
    el.querySelector(".seg").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-days]");
      if (!b) return;
      days = Number(b.dataset.days);
      el.querySelectorAll(".seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      draw();
    });
    const onResize = () => draw();
    window.addEventListener("resize", onResize);
    draw();

    return {
      async update() {
        const { quotes } = await api.quotes([symbol]);
        const q = quotes[0];
        ctx.quotes.set(q.symbol, q);
        $(el, "#c-quote").innerHTML = `
          <span class="chart-ltp ${tone(q.change)}">${esc(usd(q.price))}</span>
          <span class="${tone(q.change)}">${arrow(q.change)} ${esc(signed(q.change))} (${esc(pct(q.change_pct))})</span>`;
        $(el, "#c-ohlc").innerHTML = [
          ["Open", q.open], ["High", q.high], ["Low", q.low], ["Prev. close", q.prev_close],
        ].map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(num(v))}</dd></div>`).join("") +
          `<div><dt>Volume</dt><dd>${esc(Math.round(q.volume).toLocaleString("en-US"))}</dd></div>`;
      },
      destroy() {
        window.removeEventListener("resize", onResize);
      },
    };
  },
};

/* ------------------------------------------------------------------ Orders */

const orders = {
  title: "Orders",
  mount(el, ctx) {
    el.innerHTML = `
      <section class="card">
        <h1 class="card-title">Open orders <span class="muted" id="o-nopen"></span></h1>
        <div class="table-wrap"><table class="table">
          <thead><tr><th scope="col">Time</th><th scope="col">Type</th><th scope="col">Instrument</th>
            <th scope="col" class="r">Qty.</th><th scope="col" class="r">Limit price</th><th scope="col">Status</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
          <tbody id="o-open"></tbody></table></div>
      </section>
      <section class="card">
        <h2 class="card-title">Executed &amp; rejected orders <span class="muted" id="o-ndone"></span></h2>
        <div class="table-wrap"><table class="table">
          <thead><tr><th scope="col">Time</th><th scope="col">Type</th><th scope="col">Instrument</th>
            <th scope="col" class="r">Qty.</th><th scope="col" class="r">Avg. price</th><th scope="col">Status</th><th scope="col">Risk check</th></tr></thead>
          <tbody id="o-done"></tbody></table></div>
      </section>`;

    el.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-cancel]");
      if (!btn) return;
      setBusy(btn, true, "Cancelling…");
      try {
        await api.cancelOrder(btn.dataset.cancel);
        toast("Order cancelled.", { kind: "success" });
        ctx.refresh();
      } catch (err) {
        setBusy(btn, false);
        toast(err.message, { kind: "error", title: "Cancel failed" });
      }
    });

    return {
      async update() {
        const { orders: all } = await api.orders();
        const open = all.filter((o) => ["accepted", "pending", "submitted", "partially_filled"].includes(o.status));
        const done = all.filter((o) => !open.includes(o));
        $(el, "#o-nopen").textContent = `(${open.length})`;
        $(el, "#o-ndone").textContent = `(${done.length})`;
        $(el, "#o-open").innerHTML = open.length
          ? open.map((o) => `<tr>
              <td>${esc(clock(o.created_at))}</td><td>${sideBadge(o.side)}</td>
              <td><a class="sym-link" href="#/chart/${esc(encodeURIComponent(o.symbol))}">${esc(o.symbol)}</a> <span class="muted small">${esc(o.order_type.toUpperCase())}</span></td>
              <td class="r">${esc(qty(o.filled_quantity))} / ${esc(qty(o.quantity))}</td>
              <td class="r">${esc(usd(o.limit_price))}</td><td>${statusBadge(o.status)}</td>
              <td class="r"><button class="btn btn-small btn-ghost" type="button" data-cancel="${esc(o.id)}">Cancel</button></td></tr>`).join("")
          : emptyRow(7, "No open orders. Limit orders wait here until the price crosses.");
        $(el, "#o-done").innerHTML = done.length
          ? done.map((o) => `<tr>
              <td>${esc(clock(o.created_at))}</td><td>${sideBadge(o.side)}</td>
              <td><a class="sym-link" href="#/chart/${esc(encodeURIComponent(o.symbol))}">${esc(o.symbol)}</a> <span class="muted small">${esc(o.order_type.toUpperCase())}</span></td>
              <td class="r">${esc(qty(o.filled_quantity))} / ${esc(qty(o.requested_quantity))}</td>
              <td class="r">${esc(usd(o.average_fill_price))}</td><td>${statusBadge(o.status)}</td>
              <td>${riskBadge(o.risk_decision)}${o.risk_reason || o.risk_warnings.length ? `<div class="muted small">${esc(o.risk_reason || o.risk_warnings.join("; "))}</div>` : ""}</td></tr>`).join("")
          : emptyRow(7, "No orders yet.");
      },
    };
  },
};

/* ------------------------------------------------------------------ Positions */

const positions = {
  title: "Positions",
  mount(el, ctx) {
    el.innerHTML = `
      <section class="card">
        <h1 class="card-title">Positions <span class="muted" id="p-n"></span></h1>
        <div class="table-wrap"><table class="table">
          <thead><tr><th scope="col">Instrument</th><th scope="col" class="r">Qty.</th><th scope="col" class="r">Avg.</th>
            <th scope="col" class="r">LTP</th><th scope="col" class="r">Cur. value</th><th scope="col" class="r">P&amp;L</th>
            <th scope="col" class="r">Net chg.</th><th scope="col" class="r">Day chg.</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
          <tbody id="p-body"></tbody>
          <tfoot id="p-foot"></tfoot></table></div>
      </section>`;

    el.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-exit]");
      if (!btn) return;
      const q = Number(btn.dataset.qty);
      openOrderWindow(ctx, { symbol: btn.dataset.exit, side: q > 0 ? "sell" : "buy", quantity: Math.abs(q) });
    });

    return {
      async update() {
        const { positions: rows } = await api.positions();
        $(el, "#p-n").textContent = `(${rows.length})`;
        $(el, "#p-body").innerHTML = rows.length
          ? rows.map((r) => `<tr>
              <td><a class="sym-link" href="#/chart/${esc(encodeURIComponent(r.symbol))}">${esc(r.symbol)}</a>${r.side === "short" ? ` <span class="pill pill-warn">short</span>` : ""}</td>
              <td class="r">${esc(qty(r.quantity))}</td><td class="r">${esc(num(r.average_price))}</td>
              <td class="r">${esc(num(r.ltp))}</td><td class="r">${esc(num(r.current_value))}</td>
              <td class="r ${tone(r.pnl)}">${esc(signed(r.pnl))}</td>
              <td class="r ${tone(r.pnl_pct)}">${esc(pct(r.pnl_pct))}</td>
              <td class="r ${tone(r.day_change_pct)}">${esc(pct(r.day_change_pct))}</td>
              <td class="r"><button type="button" class="btn btn-small btn-ghost" data-exit="${esc(r.symbol)}" data-qty="${esc(r.quantity)}">Exit</button></td></tr>`).join("")
          : emptyRow(9, "No open positions. Buy from Marketwatch or a strategy signal.");
        const total = rows.reduce((s, r) => s + r.pnl, 0);
        const value = rows.reduce((s, r) => s + r.current_value, 0);
        $(el, "#p-foot").innerHTML = rows.length
          ? `<tr><th scope="row" colspan="4">Total</th><td class="r">${esc(num(value))}</td><td class="r ${tone(total)}">${esc(signed(total))}</td><td colspan="3"></td></tr>`
          : "";
      },
    };
  },
};

/* ------------------------------------------------------------------ Strategies */

const strategies = {
  title: "Strategies",
  async mount(el, ctx) {
    el.innerHTML = `
      <h1 class="page-title">Strategies</h1>
      <p class="muted page-sub">Signals are generated by the quant strategies and are informational only. Nothing is traded until you place an order, and every order still passes the risk engine.</p>
      <div id="s-list"><p class="muted">Loading strategies…</p></div>`;
    const defaults = ctx.watchlist().slice(0, 6).join(",") || "AAPL,MSFT,GOOGL,AMZN";
    const { strategies: list } = await api.strategies();

    $(el, "#s-list").innerHTML = list.map((s) => `
      <section class="card strat" data-name="${esc(s.name)}" aria-labelledby="h-${esc(s.name)}">
        <h2 class="card-title" id="h-${esc(s.name)}">${esc(s.title)}</h2>
        <p class="muted">${esc(s.description)}</p>
        <form class="strat-form">
          <label class="field field-grow"><span>Symbols</span>
            <input name="symbols" value="${esc(defaults)}" spellcheck="false" autocomplete="off" /></label>
          <label class="field"><span>Backtest period</span>
            <select name="days"><option value="365">1 year</option><option value="730" selected>2 years</option><option value="1460">4 years</option></select></label>
          <div class="strat-buttons">
            <button type="button" class="btn btn-ghost" data-run="signals">Generate signals</button>
            <button type="button" class="btn btn-primary" data-run="backtest">Run backtest</button>
          </div>
        </form>
        <div class="strat-out" aria-live="polite"></div>
      </section>`).join("");

    el.addEventListener("click", async (e) => {
      const trade = e.target.closest("[data-trade]");
      if (trade) {
        openOrderWindow(ctx, { symbol: trade.dataset.trade, side: trade.dataset.side });
        return;
      }
      const btn = e.target.closest("[data-run]");
      if (!btn) return;
      const card = btn.closest(".strat");
      const form = card.querySelector("form");
      const out = card.querySelector(".strat-out");
      const name = card.dataset.name;
      const symbols = form.symbols.value.replace(/\s+/g, "");
      setBusy(btn, true, btn.dataset.run === "signals" ? "Generating…" : "Backtesting…");
      try {
        if (btn.dataset.run === "signals") {
          const r = await api.signals(name, symbols);
          out.innerHTML = renderSignals(r);
        } else {
          const r = await api.backtest(name, { symbols, days: Number(form.days.value) });
          out.innerHTML = renderBacktest(r);
          lineChart(out.querySelector(".bt-chart"), r.equity_curve, { height: 220, label: `${name} backtest equity curve`, prefix: "$" });
        }
      } catch (err) {
        out.innerHTML = `<p class="error-text" role="alert">${esc(err.message)}</p>`;
      } finally {
        setBusy(btn, false);
      }
    });
    return {};
  },
};

function sourceNote(sources) {
  return sources.includes("simulated")
    ? `<span class="badge badge-warn">Simulated data</span>`
    : `<span class="badge">${esc(sources.map((s) => SOURCE_LABELS[s] || s).join(", "))}</span>`;
}

function renderSignals(r) {
  if (!r.signals.length) {
    return `<p class="muted">No signals right now for these symbols. ${sourceNote(r.data_sources)}</p>`;
  }
  return `
    <div class="out-head"><h3 class="out-title">Signals (${r.signals.length})</h3>${sourceNote(r.data_sources)}</div>
    <div class="table-wrap"><table class="table">
      <thead><tr><th scope="col">Instrument</th><th scope="col">Direction</th><th scope="col" class="r">Strength</th><th scope="col">Rationale</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>${r.signals.map((s) => {
        const side = s.direction === "long" ? "buy" : "sell";
        return `<tr>
          <td><a class="sym-link" href="#/chart/${esc(encodeURIComponent(s.symbol))}">${esc(s.symbol)}</a></td>
          <td><span class="pill pill-${s.direction === "long" ? "ok" : s.direction === "short" ? "bad" : "muted"}">${esc(s.direction)}</span></td>
          <td class="r"><meter min="0" max="1" value="${esc(s.strength)}" aria-label="Strength ${esc(s.strength)}"></meter> ${esc(num(s.strength, 2))}</td>
          <td class="small">${esc(s.rationale || "")}</td>
          <td class="r">${s.direction === "flat" ? "" : `<button type="button" class="btn btn-small ${side === "buy" ? "btn-buy" : "btn-sell"}" data-trade="${esc(s.symbol)}" data-side="${side}">${side === "buy" ? "Buy" : "Sell"}</button>`}</td>
        </tr>`;
      }).join("")}</tbody></table></div>`;
}

function renderBacktest(r) {
  const m = r.metrics;
  const tiles = [
    ["Total return", pct(r.total_return_pct), tone(r.total_return_pct)],
    ["CAGR", pct((m.cagr || 0) * 100), tone(m.cagr)],
    ["Sharpe ratio", num(m.sharpe_ratio), tone(m.sharpe_ratio)],
    ["Max drawdown", pct((m.max_drawdown || 0) * 100), m.max_drawdown < 0 ? "down" : "flat"],
    ["Volatility (ann.)", `${num((m.volatility_annual || 0) * 100)}%`, "flat"],
    ["Trades", qty(r.num_trades), "flat"],
  ];
  return `
    <div class="out-head"><h3 class="out-title">Backtest: ${esc(shortDate(r.start))} – ${esc(shortDate(r.end))}</h3>${sourceNote(r.data_sources)}</div>
    <div class="tiles">${tiles.map(([k, v, t]) => `<div class="tile"><div class="tile-label">${k}</div><div class="tile-value ${t}">${esc(v)}</div></div>`).join("")}</div>
    <p class="small muted">Start ${esc(usd(r.initial_capital, 0))} → end ${esc(usd(r.final_equity))}. Includes slippage and commission. Uses the same risk limits as live order entry.</p>
    <div class="bt-chart"></div>`;
}

/* ------------------------------------------------------------------ Risk */

const risk = {
  title: "Risk",
  mount(el, ctx) {
    el.innerHTML = `
      <h1 class="page-title">Risk controls</h1>
      <section class="card killswitch" id="r-ks" aria-labelledby="r-ks-h">
        <div>
          <h2 class="card-title" id="r-ks-h">Trading status</h2>
          <p id="r-state" class="ks-state">—</p>
        </div>
        <form id="r-halt-form" class="ks-form">
          <label class="field field-grow"><span>Halt reason</span>
            <input name="reason" maxlength="200" value="Manual halt from dashboard" /></label>
          <button type="submit" class="btn btn-danger" id="r-halt">Halt trading</button>
        </form>
        <button type="button" class="btn btn-primary" id="r-resume" hidden>Resume trading</button>
      </section>

      <div class="grid-2">
        <section class="card" aria-labelledby="r-lim-h">
          <h2 class="card-title" id="r-lim-h">Limits</h2>
          <p class="muted small">Deterministic checks. AI agents can't override them.</p>
          <dl class="kv" id="r-limits"></dl>
        </section>
        <section class="card" aria-labelledby="r-how-h">
          <h2 class="card-title" id="r-how-h">Check order</h2>
          <ol class="steps small">
            <li>Trading halt</li><li>Daily loss limit (auto-halts)</li><li>Max drawdown (blocks new buys)</li>
            <li>Position size (reduces quantity)</li><li>Top-3 concentration</li><li>Gross leverage</li>
          </ol>
          <p class="muted small">Orders that reduce an existing position are always allowed through the size, concentration and leverage checks.</p>
        </section>
      </div>

      <section class="card" aria-labelledby="r-log-h">
        <h2 class="card-title" id="r-log-h">Decision log</h2>
        <div class="table-wrap"><table class="table">
          <thead><tr><th scope="col">Time</th><th scope="col">Order</th><th scope="col" class="r">Requested</th><th scope="col" class="r">Authorized</th><th scope="col">Decision</th><th scope="col">Details</th></tr></thead>
          <tbody id="r-log"></tbody></table></div>
      </section>`;

    $(el, "#r-halt-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const reason = e.target.reason.value.trim() || "Manual halt from dashboard";
      const btn = $(el, "#r-halt");
      setBusy(btn, true, "Halting…");
      try {
        await api.halt(reason);
        toast("All new orders will be rejected until you resume.", { kind: "warn", title: "Trading halted" });
        ctx.refresh();
      } catch (err) {
        toast(err.message, { kind: "error", title: "Halt failed" });
      } finally {
        setBusy(btn, false);
      }
    });
    $(el, "#r-resume").addEventListener("click", async (e) => {
      setBusy(e.target, true, "Resuming…");
      try {
        await api.resume();
        toast("Orders are accepted again.", { kind: "success", title: "Trading resumed" });
        ctx.refresh();
      } catch (err) {
        toast(err.message, { kind: "error", title: "Resume failed" });
      } finally {
        setBusy(e.target, false);
      }
    });

    return {
      async update() {
        const [s, log] = await Promise.all([api.riskStatus(), api.riskLog(50)]);
        const halted = s.trading_halted;
        $(el, "#r-ks").classList.toggle("halted", halted);
        $(el, "#r-state").innerHTML = halted
          ? `<span class="pill pill-bad">Halted</span> ${esc(s.halt_reason || "")}`
          : `<span class="pill pill-ok">Active</span> Orders are being accepted (subject to limits).`;
        $(el, "#r-halt-form").hidden = halted;
        $(el, "#r-resume").hidden = !halted;
        const L = s.limits;
        $(el, "#r-limits").innerHTML = [
          ["Max position size", `${num(L.max_position_pct * 100, 0)}% of equity`],
          ["Max drawdown", `${num(L.max_drawdown_pct * 100, 0)}% from peak`],
          ["Daily loss halt", `${num(L.max_daily_loss_pct * 100, 0)}% of equity`],
          ["Max top-3 concentration", `${num(L.max_top3_concentration_pct * 100, 0)}%`],
          ["Max gross leverage", `${num(L.max_leverage, 1)}×`],
          ["Mode", s.trading_mode],
        ].map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("");
        $(el, "#r-log").innerHTML = log.entries.length
          ? log.entries.map((e) => `<tr>
              <td>${esc(clock(e.timestamp))}</td><td>${sideBadge(e.side)} ${esc(e.symbol)}</td>
              <td class="r">${esc(qty(e.requested_qty))}</td><td class="r">${esc(qty(e.authorized_qty))}</td>
              <td>${riskBadge(e.decision)}</td>
              <td><details><summary>${esc(e.reason || (e.warnings[0] || "All checks passed"))}</summary>
                <ul class="checks">${e.checks.map((c) => `<li class="${c.passed ? "up" : "down"}">${c.passed ? "✓" : "✗"} <strong>${esc(c.name)}</strong>: ${esc(c.message)}</li>`).join("")}</ul>
              </details></td></tr>`).join("")
          : emptyRow(6, "No decisions yet. Each order you place is logged here with every check it went through.");
      },
    };
  },
};

/* ------------------------------------------------------------------ Funds */

const funds = {
  title: "Funds",
  mount(el, ctx) {
    el.innerHTML = `
      <h1 class="page-title">Funds</h1>
      <div class="grid-2">
        <section class="card" aria-labelledby="f-eq-h">
          <h2 class="card-title" id="f-eq-h">Equity (paper account)</h2>
          <div class="kpi"><span class="kpi-value" id="f-cash">—</span><span class="kpi-label">Available cash</span></div>
          <table class="table table-kv"><tbody id="f-body"></tbody></table>
        </section>
        <section class="card" aria-labelledby="f-reset-h">
          <h2 class="card-title" id="f-reset-h">Reset paper account</h2>
          <p class="muted">Closes everything and restores the opening balance. Clears positions, orders and the risk log. Paper only; no real money.</p>
          <button type="button" class="btn btn-ghost" id="f-reset">Reset account</button>
          <p class="muted small">This account lives in the engine's memory and also resets when the server restarts.</p>
        </section>
      </div>`;

    $(el, "#f-reset").addEventListener("click", async (e) => {
      if (!window.confirm("Reset the paper account? Positions, orders and the risk log will be cleared.")) return;
      setBusy(e.target, true, "Resetting…");
      try {
        await api.resetAccount();
        toast("Paper account restored to the opening balance.", { kind: "success", title: "Account reset" });
        ctx.refresh();
      } catch (err) {
        toast(err.message, { kind: "error", title: "Reset failed" });
      } finally {
        setBusy(e.target, false);
      }
    });

    return {
      async update() {
        const p = await api.portfolio();
        ctx.cash = p.cash;
        $(el, "#f-cash").textContent = usd(p.cash);
        $(el, "#f-body").innerHTML = [
          ["Opening balance", usd(p.initial_capital), ""],
          ["Available cash", usd(p.cash), ""],
          ["Used (invested at cost)", usd(p.invested), ""],
          ["Holdings value", usd(p.current_value), ""],
          ["Account value", usd(p.equity), ""],
          ["Unrealised P&L", signedUsd(p.unrealized_pnl), tone(p.unrealized_pnl)],
          ["Total P&L since start", `${signedUsd(p.total_pnl)} (${pct(p.total_pnl_pct)})`, tone(p.total_pnl)],
          ["Commission paid", usd(p.commission_paid), ""],
        ].map(([k, v, t]) => `<tr><th scope="row">${k}</th><td class="r ${t}">${esc(v)}</td></tr>`).join("");
      },
    };
  },
};

export const views = { dashboard, chart, orders, positions, strategies, risk, funds };
