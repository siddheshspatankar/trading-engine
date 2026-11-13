// Kite-style order window (modal dialog). Every order goes through the engine's risk gate.

import { api } from "./api.js";
import { arrow, esc, pct, qty as fmtQty, setBusy, toast, tone, usd } from "./util.js";

let active = null; // { close }

/**
 * @param {object} ctx  app context ({ quotes: Map, refresh(), cash })
 * @param {object} opts { symbol, side: 'buy'|'sell', quantity?, price? }
 */
export function openOrderWindow(ctx, { symbol, side = "buy", quantity = 1, price = null } = {}) {
  if (active) active.close();
  const opener = document.activeElement;
  const root = document.getElementById("modal-root");
  const quote = ctx.quotes.get(symbol);

  root.innerHTML = `
    <div class="ow-backdrop">
      <div class="ow" role="dialog" aria-modal="true" aria-labelledby="ow-title">
        <header class="ow-head">
          <div>
            <h2 id="ow-title" class="ow-title"></h2>
            <div class="ow-ltp" id="ow-ltp"></div>
          </div>
          <label class="ow-switch" title="Toggle buy / sell">
            <span class="sr-only">Sell instead of buy</span>
            <input type="checkbox" id="ow-side" role="switch" />
            <span class="ow-switch-track" aria-hidden="true"><span class="ow-switch-knob"></span></span>
          </label>
        </header>

        <form class="ow-form" novalidate>
          <fieldset class="ow-types">
            <legend class="sr-only">Order type</legend>
            <label><input type="radio" name="otype" value="market" checked /> Market</label>
            <label><input type="radio" name="otype" value="limit" /> Limit</label>
          </fieldset>

          <div class="ow-fields">
            <label class="field">
              <span>Qty.</span>
              <input id="ow-qty" type="number" inputmode="numeric" min="1" max="1000000" step="1" required />
            </label>
            <label class="field">
              <span>Price</span>
              <input id="ow-price" type="number" inputmode="decimal" min="0.01" step="0.01" disabled />
            </label>
          </div>

          <p class="ow-error" id="ow-error" role="alert" hidden></p>
          <p class="ow-note">Checked by the risk engine, then sent to the paper broker. No real money is used.</p>

          <footer class="ow-foot">
            <div class="ow-margin">
              <div>Approx. value <strong id="ow-required">—</strong></div>
              <div class="muted">Cash available <span id="ow-available">—</span></div>
            </div>
            <div class="ow-actions">
              <button type="submit" class="btn" id="ow-submit"></button>
              <button type="button" class="btn btn-ghost" id="ow-cancel">Cancel</button>
            </div>
          </footer>
        </form>
      </div>
    </div>`;

  const dialog = root.querySelector(".ow");
  const sideToggle = root.querySelector("#ow-side");
  const qtyInput = root.querySelector("#ow-qty");
  const priceInput = root.querySelector("#ow-price");
  const submit = root.querySelector("#ow-submit");
  const errorEl = root.querySelector("#ow-error");
  const form = root.querySelector("form");

  let currentSide = side;
  sideToggle.checked = side === "sell";
  qtyInput.value = Math.max(1, Math.floor(quantity));
  const ltp = quote ? quote.price : price;
  priceInput.value = price ?? (ltp ? ltp.toFixed(2) : "");

  const orderType = () => form.querySelector('input[name="otype"]:checked').value;

  function render() {
    dialog.classList.toggle("ow-buy", currentSide === "buy");
    dialog.classList.toggle("ow-sell", currentSide === "sell");
    const q = Number(qtyInput.value) || 0;
    root.querySelector("#ow-title").innerHTML =
      `${currentSide === "buy" ? "Buy" : "Sell"} ${esc(symbol)} <small>× ${esc(fmtQty(q))} Qty</small>`;
    const qt = ctx.quotes.get(symbol);
    root.querySelector("#ow-ltp").innerHTML = qt
      ? `LTP ${esc(usd(qt.price))} <span class="${tone(qt.change_pct)}">${arrow(qt.change_pct)} ${esc(pct(qt.change_pct))}</span>`
      : "LTP unavailable";
    priceInput.disabled = orderType() === "market";
    const px = orderType() === "limit" ? Number(priceInput.value) : qt ? qt.price : 0;
    root.querySelector("#ow-required").textContent = px && q ? usd(px * q) : "—";
    root.querySelector("#ow-available").textContent = ctx.cash != null ? usd(ctx.cash) : "—";
    submit.textContent = currentSide === "buy" ? "Buy" : "Sell";
    submit.className = `btn ${currentSide === "buy" ? "btn-buy" : "btn-sell"}`;
  }

  function close() {
    document.removeEventListener("keydown", onKey, true);
    root.innerHTML = "";
    active = null;
    if (opener && document.contains(opener)) opener.focus();
  }

  function onKey(e) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "Tab") {
      const focusable = [...dialog.querySelectorAll("input:not([disabled]), button:not([disabled])")];
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  sideToggle.addEventListener("change", () => {
    currentSide = sideToggle.checked ? "sell" : "buy";
    render();
  });
  form.addEventListener("input", render);
  form.addEventListener("change", render);
  root.querySelector("#ow-cancel").addEventListener("click", close);
  root.querySelector(".ow-backdrop").addEventListener("mousedown", (e) => {
    if (e.target === e.currentTarget) close();
  });
  document.addEventListener("keydown", onKey, true);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.hidden = true;
    const q = Number(qtyInput.value);
    if (!Number.isInteger(q) || q < 1) {
      errorEl.textContent = "Enter a whole number of shares (at least 1).";
      errorEl.hidden = false;
      qtyInput.focus();
      return;
    }
    const body = { symbol, side: currentSide, quantity: q, order_type: orderType() };
    if (body.order_type === "limit") {
      const px = Number(priceInput.value);
      if (!(px > 0)) {
        errorEl.textContent = "Enter a limit price above 0.";
        errorEl.hidden = false;
        priceInput.focus();
        return;
      }
      body.limit_price = Math.round(px * 100) / 100;
    }

    setBusy(submit, true, "Placing…");
    try {
      const order = await api.placeOrder(body);
      close();
      announceOrder(order);
      ctx.refresh();
    } catch (err) {
      setBusy(submit, false);
      errorEl.textContent = err.message || "Order failed";
      errorEl.hidden = false;
    }
  });

  render();
  qtyInput.focus();
  qtyInput.select();
  active = { close };
}

function announceOrder(o) {
  const head = `${o.side.toUpperCase()} ${fmtQty(o.quantity)} ${o.symbol}`;
  if (o.status === "rejected") {
    toast(o.risk_reason || "Order rejected", { kind: "error", title: `${head} rejected`, timeout: 9000 });
    return;
  }
  const notes = [];
  if (o.risk_decision === "modified") {
    notes.push(`Risk engine reduced quantity ${fmtQty(o.requested_quantity)} → ${fmtQty(o.quantity)}.`);
  }
  if (o.status === "filled") {
    toast([`Filled at ${usd(o.average_fill_price)}.`, ...notes].join(" "), {
      kind: o.risk_decision === "modified" ? "warn" : "success",
      title: `${head} complete`,
    });
  } else if (o.status === "partially_filled") {
    toast([`Partially filled ${fmtQty(o.filled_quantity)} at ${usd(o.average_fill_price)}.`, ...notes].join(" "), {
      kind: "warn",
      title: head,
    });
  } else if (o.status === "accepted") {
    toast([`Limit order open at ${usd(o.limit_price)}.`, ...notes].join(" "), { title: `${head} open` });
  } else {
    toast(`Status: ${o.status}. ${o.risk_reason || ""}`, { kind: "warn", title: head });
  }
}
