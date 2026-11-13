// Formatting, escaping and small UI helpers.

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** Escape any value for safe interpolation into HTML. */
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

const isNum = (n) => typeof n === "number" && Number.isFinite(n);

export const num = (n, digits = 2) =>
  isNum(n)
    ? n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : "—";

export const usd = (n, digits = 2) => (isNum(n) ? `${n < 0 ? "−" : ""}$${num(Math.abs(n), digits)}` : "—");

/** Signed number with an explicit + / − (so direction never relies on color alone). */
export const signed = (n, digits = 2) =>
  isNum(n) ? `${n > 0 ? "+" : n < 0 ? "−" : ""}${num(Math.abs(n), digits)}` : "—";

export const signedUsd = (n) => (isNum(n) ? `${n > 0 ? "+" : n < 0 ? "−" : ""}$${num(Math.abs(n))}` : "—");

export const pct = (n, digits = 2) =>
  isNum(n) ? `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(digits)}%` : "—";

export const tone = (n) => (!isNum(n) || n === 0 ? "flat" : n > 0 ? "up" : "down");

export const arrow = (n) => (!isNum(n) || n === 0 ? "" : n > 0 ? "▲" : "▼");

export const qty = (n) => (isNum(n) ? n.toLocaleString("en-US", { maximumFractionDigits: 0 }) : "—");

export function clock(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function shortDate(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export const SOURCE_LABELS = { alpaca_iex: "Alpaca IEX", simulated: "Simulated data" };

/** Show a transient message. kind: info | success | error | warn */
export function toast(message, { kind = "info", title = "", timeout = 6000 } = {}) {
  const root = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = `toast toast-${kind}`;
  el.setAttribute("role", kind === "error" ? "alert" : "status");
  el.innerHTML = `
    ${title ? `<strong class="toast-title">${esc(title)}</strong>` : ""}
    <div class="toast-body">${esc(message)}</div>
    <button class="toast-close" type="button" aria-label="Dismiss">×</button>`;
  el.querySelector(".toast-close").addEventListener("click", () => el.remove());
  root.prepend(el);
  [...root.children].slice(3).forEach((old) => old.remove()); // keep at most 3 visible
  if (timeout) setTimeout(() => el.remove(), timeout);
}

export function setBusy(button, busy, busyLabel) {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = busyLabel || "Working…";
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  } else {
    button.textContent = button.dataset.label || button.textContent;
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}
