# PC Price Tracker

Local app that tracks laptop **models, specs, and prices**, and shows how much the price moved vs last week (or 2 weeks / 30 days).

## Features

- Dashboard: model · chip · RAM · storage · display · current price · previous price · delta %
- 30-day sparklines
- Week-over-week (or custom) comparison
- Product catalog in `data/products.yaml` (edit freely)
- Best-effort live scrape of product pages
- Manual price entry when scrape fails
- SQLite history in `data/prices.db`
- Optional daily auto-refresh at 09:00 (while app is running)

## Quick start

```powershell
cd pc-price-tracker
.\run.ps1
```

Default URL is **http://127.0.0.1:8877** (auto-picks a free port if busy).

Force a port:

```powershell
$env:PORT = 9000
.\run.ps1
```

## Add models

Edit `data/products.yaml`, then restart the app (catalog re-syncs on startup).

## APIs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/dashboard?days=7` | Table data + deltas |
| GET | `/api/history/{id}?days=90` | Price history |
| POST | `/api/refresh` | Live scrape all products |
| POST | `/api/price` | Manual price `{product_id, price}` |
| POST | `/api/seed?force=true` | Re-generate demo history |

## Notes on scraping

Vendor sites (Apple, Dell, Lenovo, HP) often block or reshape HTML. On first run the app **seeds realistic demo history** so the dashboard and week-over-week deltas work immediately. Use **Refresh prices** for live attempts, or enter prices manually.

For production fleet pricing, vendor quote portals / CDW / reseller APIs are more reliable than public PDP scrapes.

## Docker

Build and run locally with Compose (persists SQLite in a volume):

```powershell
cd pc-price-tracker
docker compose up --build
```

Open: http://127.0.0.1:8877

Or plain Docker:

```powershell
docker build -t pc-price-tracker:local .
docker run --rm -p 8877:8000 -v ppt-data:/data pc-price-tracker:local
```

### Push to a registry (example)

```powershell
docker build -t ghcr.io/YOUR_ORG/pc-price-tracker:1.0.0 .
docker push ghcr.io/YOUR_ORG/pc-price-tracker:1.0.0
```

Then set that image in `k8s/kustomization.yaml` / `k8s/deployment.yaml`.

## Kubernetes

Manifests live in [`k8s/`](k8s/):

| File | Purpose |
|------|---------|
| `namespace.yaml` | Namespace |
| `configmap.yaml` | Product catalog (`products.yaml`) |
| `pvc.yaml` | Persistent volume for SQLite |
| `deployment.yaml` | Single-replica app (required for SQLite) |
| `service.yaml` | ClusterIP service |
| `ingress.yaml` | Optional external URL |
| `cronjob-refresh.yaml` | Optional daily `/api/refresh` |
| `kustomization.yaml` | Apply everything together |

### Deploy

```powershell
# 1) Build image (for local cluster: load into kind/minikube/k3d)
docker build -t pc-price-tracker:local .

# kind example:
# kind load docker-image pc-price-tracker:local

# 2) Point kustomization at your registry image if not local
# 3) Apply
kubectl apply -k k8s/

# 4) Port-forward to try it
kubectl -n pc-price-tracker port-forward svc/pc-price-tracker 8877:80
```

Then open http://127.0.0.1:8877

### Design notes for k8s

- **Replicas = 1** + `Recreate` strategy: SQLite cannot safely multi-write across pods.
- **PVC** at `/data` holds `prices.db` so restarts keep history.
- **ConfigMap** mounts `products.yaml` (edit catalog without rebuilding the image).
- **CronJob** (optional) is better than relying only on the in-process scheduler if pods restart often.
- For multi-replica / HA later: move history to Postgres and scale the Deployment.

### Env vars

| Variable | Default | Meaning |
|----------|---------|---------|
| `DATA_DIR` | `/data` (container) | Data directory |
| `DB_PATH` | `$DATA_DIR/prices.db` | SQLite file |
| `CATALOG_PATH` | `$DATA_DIR/products.yaml` | Product catalog |
| `PORT` | `8000` | Listen port |
| `TZ` | (host) | Timezone for cron/logs |
