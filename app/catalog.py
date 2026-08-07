"""Load product catalog from YAML into the DB."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .db import DATA_DIR, init_db, upsert_product

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = Path(
    os.environ.get("CATALOG_PATH", DATA_DIR / "products.yaml")
)


def load_catalog() -> list[dict[str, Any]]:
    if not CATALOG_PATH.exists():
        raise FileNotFoundError(f"Catalog not found: {CATALOG_PATH}")
    with CATALOG_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    products = data.get("products") or []
    if not isinstance(products, list):
        raise ValueError("products.yaml must contain a 'products' list")
    return products


def sync_catalog() -> int:
    init_db()
    products = load_catalog()
    for p in products:
        upsert_product(p)
    return len(products)
