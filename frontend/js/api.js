// Thin wrapper over the engine's REST API (same origin as the dashboard).

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Engine unreachable");
  }

  let data = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON body */
  }

  if (!res.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) {
      detail = detail.map((d) => String(d.msg || "").replace(/^Value error, /, "")).join("; ");
    }
    throw new ApiError(res.status, detail || (data && data.message) || res.statusText);
  }
  return data;
}

const q = encodeURIComponent;

export const api = {
  health: () => request("GET", "/health/"),
  marketStatus: () => request("GET", "/market/status"),
  indices: () => request("GET", "/market/indices"),
  quotes: (symbols) => request("GET", `/market/quotes?symbols=${q(symbols.join(","))}`),
  bars: (symbol, days) => request("GET", `/market/bars/${q(symbol)}?days=${days}`),

  portfolio: () => request("GET", "/portfolio/"),
  positions: () => request("GET", "/portfolio/positions"),
  resetAccount: () => request("POST", "/portfolio/reset"),

  orders: () => request("GET", "/orders"),
  placeOrder: (order) => request("POST", "/orders", order),
  cancelOrder: (id) => request("DELETE", `/orders/${q(id)}`),

  strategies: () => request("GET", "/strategies/"),
  signals: (name, symbols) => request("GET", `/strategies/${q(name)}/signals?symbols=${q(symbols)}`),
  backtest: (name, body) => request("POST", `/strategies/${q(name)}/backtest`, body),

  riskStatus: () => request("GET", "/risk/status"),
  riskLog: (limit = 50) => request("GET", `/risk/log?limit=${limit}`),
  halt: (reason) => request("POST", `/risk/halt?reason=${q(reason)}`),
  resume: () => request("POST", "/risk/resume"),
};
