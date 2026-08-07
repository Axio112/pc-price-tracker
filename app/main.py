"""Local PC price tracker dashboard (FastAPI)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .catalog import sync_catalog
from .db import dashboard_rows, init_db, list_products, price_history
from .scraper import record_manual_price, refresh_all_prices, seed_history

app = FastAPI(title="PC Price Tracker", version="1.0.0")
scheduler = BackgroundScheduler()
ROOT = Path(__file__).resolve().parent.parent


class ManualPriceIn(BaseModel):
    product_id: str
    price: float = Field(gt=0, lt=50000)
    currency: str = "USD"


def _bootstrap() -> None:
    init_db()
    sync_catalog()
    seed_history(days=30)


@app.on_event("startup")
def on_startup() -> None:
    _bootstrap()
    # Daily refresh at 09:00 local — best effort; failures are stored as scrape-failed
    if not scheduler.running:
        scheduler.add_job(
            lambda: __import__("asyncio").run(refresh_all_prices()),
            trigger="cron",
            hour=9,
            minute=0,
            id="daily_refresh",
            replace_existing=True,
        )
        scheduler.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/products")
def api_products() -> list[dict[str, Any]]:
    return list_products()


@app.get("/api/dashboard")
def api_dashboard(days: int = Query(7, ge=1, le=90)) -> list[dict[str, Any]]:
    return dashboard_rows(compare_days=days)


@app.get("/api/history/{product_id}")
def api_history(product_id: str, days: int = Query(90, ge=7, le=365)) -> list[dict[str, Any]]:
    rows = price_history(product_id, days=days)
    # Only chart successful prices
    return [r for r in rows if r.get("scrape_status") == "ok"]


@app.post("/api/refresh")
async def api_refresh() -> dict[str, Any]:
    results = await refresh_all_prices()
    ok = sum(1 for r in results if r["status"] == "ok")
    return {"ok": ok, "total": len(results), "results": results}


@app.post("/api/seed")
def api_seed(force: bool = False) -> dict[str, Any]:
    n = seed_history(days=30, force=force)
    return {"inserted": n}


@app.post("/api/price")
def api_manual_price(body: ManualPriceIn) -> dict[str, Any]:
    ids = {p["id"] for p in list_products()}
    if body.product_id not in ids:
        raise HTTPException(404, f"Unknown product_id: {body.product_id}")
    record_manual_price(body.product_id, body.price, body.currency)
    return {"status": "ok", "product_id": body.product_id, "price": body.price}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PC Price Tracker</title>
  <style>
    :root {
      --bg: #0b1220;
      --panel: #121a2b;
      --panel-2: #182338;
      --border: #263352;
      --text: #e8eefc;
      --muted: #93a0bd;
      --accent: #5b9dff;
      --good: #3dd68c;
      --bad: #ff6b7a;
      --warn: #f5c451;
      --chip: #1e2b45;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
      background: radial-gradient(1200px 600px at 10% -10%, #1a2744 0%, var(--bg) 55%);
      color: var(--text);
      min-height: 100vh;
    }
    header {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
      padding: 20px 24px 8px;
    }
    h1 {
      margin: 0;
      font-size: 1.45rem;
      font-weight: 650;
      letter-spacing: -0.02em;
    }
    .sub { color: var(--muted); font-size: 0.92rem; margin-top: 4px; }
    .controls {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }
    select, button, input {
      background: var(--panel);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 9px 12px;
      font-size: 0.92rem;
    }
    button {
      cursor: pointer;
      background: linear-gradient(180deg, #3a7bd5 0%, #1f4f9a 100%);
      border-color: #3d6db8;
      font-weight: 600;
    }
    button.secondary {
      background: var(--panel-2);
      border-color: var(--border);
      font-weight: 500;
    }
    button:disabled { opacity: 0.55; cursor: wait; }
    main { padding: 12px 24px 40px; }
    .cards {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px 16px;
    }
    .card .label { color: var(--muted); font-size: 0.8rem; }
    .card .value { font-size: 1.35rem; font-weight: 700; margin-top: 4px; }
    .table-wrap {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      overflow: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 980px;
    }
    th, td {
      text-align: left;
      padding: 12px 14px;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
    }
    th {
      position: sticky;
      top: 0;
      background: #152038;
      color: var(--muted);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      font-weight: 600;
    }
    tr:hover td { background: rgba(91, 157, 255, 0.05); }
    .model { font-weight: 650; }
    .brand { color: var(--muted); font-size: 0.85rem; }
    .specs {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 6px;
    }
    .pill {
      background: var(--chip);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 0.75rem;
      color: #c5d2ef;
    }
    .price { font-variant-numeric: tabular-nums; font-weight: 650; white-space: nowrap; }
    .delta {
      font-variant-numeric: tabular-nums;
      font-weight: 650;
      white-space: nowrap;
    }
    .delta.up { color: var(--bad); }
    .delta.down { color: var(--good); }
    .delta.flat { color: var(--muted); }
    .spark {
      width: 120px;
      height: 36px;
      display: block;
    }
    .status {
      margin-top: 12px;
      color: var(--muted);
      font-size: 0.88rem;
      min-height: 1.2em;
    }
    .status.err { color: var(--bad); }
    .status.ok { color: var(--good); }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .manual {
      display: flex;
      gap: 6px;
      align-items: center;
      flex-wrap: wrap;
    }
    .manual input { width: 100px; padding: 6px 8px; }
    .manual button { padding: 6px 10px; font-size: 0.8rem; }
    footer {
      color: var(--muted);
      font-size: 0.8rem;
      padding: 0 24px 24px;
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>PC Price Tracker</h1>
      <div class="sub">Local dashboard · model · specs · price · change vs previous period</div>
    </div>
    <div class="controls">
      <label>
        Compare
        <select id="days">
          <option value="7" selected>vs last week</option>
          <option value="14">vs 2 weeks</option>
          <option value="30">vs last month</option>
        </select>
      </label>
      <button id="btnRefresh" title="Best-effort live scrape">Refresh prices</button>
      <button id="btnReload" class="secondary">Reload</button>
    </div>
  </header>

  <main>
    <div class="cards" id="summary"></div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Model</th>
            <th>Specs</th>
            <th>Current</th>
            <th>Was</th>
            <th>Change</th>
            <th>Trend</th>
            <th>Source / link</th>
            <th>Manual</th>
          </tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
    <div class="status" id="status"></div>
  </main>
  <footer>
    Seed history is generated on first run so week-over-week works immediately.
    Live scrapes depend on vendor pages and may fail; use manual price entry or edit <code>data/products.yaml</code>.
  </footer>

  <script>
    const $ = (id) => document.getElementById(id);
    const money = (n, c = 'USD') => n == null ? '—' :
      new Intl.NumberFormat('en-US', { style: 'currency', currency: c || 'USD', maximumFractionDigits: 0 }).format(n);

    function deltaClass(d) {
      if (d == null) return 'flat';
      if (d > 0) return 'up';
      if (d < 0) return 'down';
      return 'flat';
    }

    function deltaText(row) {
      if (row.delta == null) return '—';
      const sign = row.delta > 0 ? '+' : '';
      const pct = row.delta_pct == null ? '' : ` (${sign}${row.delta_pct}%)`;
      return `${sign}${money(row.delta, row.currency)}${pct}`;
    }

    function sparkline(points) {
      if (!points || points.length < 2) {
        return '<svg class="spark"></svg>';
      }
      const w = 120, h = 36, pad = 2;
      const vals = points.map(p => p.price);
      const min = Math.min(...vals), max = Math.max(...vals);
      const span = (max - min) || 1;
      const coords = vals.map((v, i) => {
        const x = pad + (i * (w - pad * 2)) / (vals.length - 1);
        const y = h - pad - ((v - min) / span) * (h - pad * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      });
      const color = vals[vals.length - 1] >= vals[0] ? '#ff6b7a' : '#3dd68c';
      return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <polyline fill="none" stroke="${color}" stroke-width="2"
          points="${coords.join(' ')}" /></svg>`;
    }

    async function loadHistory(id) {
      const res = await fetch(`/api/history/${encodeURIComponent(id)}?days=30`);
      return res.json();
    }

    async function loadDashboard() {
      const days = $('days').value;
      $('status').textContent = 'Loading…';
      $('status').className = 'status';
      const res = await fetch(`/api/dashboard?days=${days}`);
      const rows = await res.json();

      const withPrice = rows.filter(r => r.current_price != null);
      const drops = withPrice.filter(r => (r.delta ?? 0) < 0).length;
      const rises = withPrice.filter(r => (r.delta ?? 0) > 0).length;
      const avgDelta = withPrice.length
        ? withPrice.reduce((s, r) => s + (r.delta || 0), 0) / withPrice.length
        : null;

      $('summary').innerHTML = `
        <div class="card"><div class="label">Models tracked</div><div class="value">${rows.length}</div></div>
        <div class="card"><div class="label">With price</div><div class="value">${withPrice.length}</div></div>
        <div class="card"><div class="label">Dropped vs period</div><div class="value" style="color:var(--good)">${drops}</div></div>
        <div class="card"><div class="label">Rose vs period</div><div class="value" style="color:var(--bad)">${rises}</div></div>
        <div class="card"><div class="label">Avg change</div><div class="value">${avgDelta == null ? '—' : money(avgDelta)}</div></div>
      `;

      const histories = await Promise.all(rows.map(r => loadHistory(r.id)));
      const tbody = $('rows');
      tbody.innerHTML = rows.map((r, i) => {
        const hist = histories[i] || [];
        const storage = r.storage_gb >= 1024 ? `${(r.storage_gb/1024).toFixed(0)} TB` : `${r.storage_gb} GB`;
        return `<tr>
          <td>
            <div class="model">${r.model}</div>
            <div class="brand">${r.brand}${r.category ? ' · ' + r.category : ''}</div>
          </td>
          <td>
            <div class="specs">
              <span class="pill">${r.chip || '—'}</span>
              <span class="pill">${r.ram_gb || '—'} GB RAM</span>
              <span class="pill">${storage}</span>
              <span class="pill">${r.display || '—'}</span>
            </div>
          </td>
          <td class="price">${money(r.current_price, r.currency)}</td>
          <td class="price">${money(r.past_price, r.currency)}</td>
          <td class="delta ${deltaClass(r.delta)}">${deltaText(r)}</td>
          <td>${sparkline(hist)}</td>
          <td>
            <div>${r.price_source || '—'}</div>
            ${r.url ? `<div><a href="${r.url}" target="_blank" rel="noopener">Product page</a></div>` : ''}
            <div class="brand">${r.last_scraped ? 'as of ' + r.last_scraped.replace('T',' ').replace('Z','') : ''}</div>
          </td>
          <td>
            <div class="manual">
              <input type="number" min="1" step="1" placeholder="price" data-id="${r.id}" />
              <button data-save="${r.id}">Save</button>
            </div>
          </td>
        </tr>`;
      }).join('');

      tbody.querySelectorAll('button[data-save]').forEach(btn => {
        btn.addEventListener('click', async () => {
          const id = btn.getAttribute('data-save');
          const input = tbody.querySelector(`input[data-id="${id}"]`);
          const price = parseFloat(input.value);
          if (!price || price <= 0) {
            $('status').textContent = 'Enter a valid price';
            $('status').className = 'status err';
            return;
          }
          const res = await fetch('/api/price', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ product_id: id, price })
          });
          if (!res.ok) {
            $('status').textContent = 'Failed to save price';
            $('status').className = 'status err';
            return;
          }
          $('status').textContent = `Saved manual price for ${id}`;
          $('status').className = 'status ok';
          await loadDashboard();
        });
      });

      $('status').textContent = `Showing ${rows.length} models · compare window ${days} days`;
      $('status').className = 'status';
    }

    $('days').addEventListener('change', loadDashboard);
    $('btnReload').addEventListener('click', loadDashboard);
    $('btnRefresh').addEventListener('click', async () => {
      const btn = $('btnRefresh');
      btn.disabled = true;
      $('status').textContent = 'Refreshing (live scrape, may take a bit)…';
      $('status').className = 'status';
      try {
        const res = await fetch('/api/refresh', { method: 'POST' });
        const data = await res.json();
        $('status').textContent = `Refresh done: ${data.ok}/${data.total} live prices found. Failed rows keep last good seed/manual price in the table (failed attempts are logged).`;
        $('status').className = data.ok ? 'status ok' : 'status err';
        await loadDashboard();
      } catch (e) {
        $('status').textContent = 'Refresh failed: ' + e;
        $('status').className = 'status err';
      } finally {
        btn.disabled = false;
      }
    });

    loadDashboard();
  </script>
</body>
</html>
"""
