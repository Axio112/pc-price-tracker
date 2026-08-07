"""SQLite storage for products and price history."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import os

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("DB_PATH", DATA_DIR / "prices.db"))


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                brand TEXT NOT NULL,
                model TEXT NOT NULL,
                chip TEXT,
                ram_gb INTEGER,
                storage_gb INTEGER,
                display TEXT,
                category TEXT,
                source TEXT,
                url TEXT,
                currency TEXT DEFAULT 'USD',
                notes TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS price_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL,
                price REAL NOT NULL,
                currency TEXT DEFAULT 'USD',
                source TEXT,
                scraped_at TEXT NOT NULL,
                scrape_status TEXT DEFAULT 'ok',
                raw_note TEXT,
                FOREIGN KEY (product_id) REFERENCES products(id)
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_product_time
                ON price_snapshots(product_id, scraped_at);
            """
        )


def upsert_product(product: dict[str, Any]) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO products (
                id, brand, model, chip, ram_gb, storage_gb, display,
                category, source, url, currency, notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                brand=excluded.brand,
                model=excluded.model,
                chip=excluded.chip,
                ram_gb=excluded.ram_gb,
                storage_gb=excluded.storage_gb,
                display=excluded.display,
                category=excluded.category,
                source=excluded.source,
                url=excluded.url,
                currency=excluded.currency,
                notes=excluded.notes,
                updated_at=excluded.updated_at
            """,
            (
                product["id"],
                product["brand"],
                product["model"],
                product.get("chip"),
                product.get("ram_gb"),
                product.get("storage_gb"),
                product.get("display"),
                product.get("category"),
                product.get("source"),
                product.get("url"),
                product.get("currency", "USD"),
                product.get("notes"),
                datetime.utcnow().isoformat(timespec="seconds") + "Z",
            ),
        )


def insert_price(
    product_id: str,
    price: float,
    *,
    currency: str = "USD",
    source: str = "scrape",
    scraped_at: datetime | None = None,
    scrape_status: str = "ok",
    raw_note: str | None = None,
) -> None:
    ts = scraped_at or datetime.utcnow()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO price_snapshots (
                product_id, price, currency, source, scraped_at, scrape_status, raw_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id,
                float(price),
                currency,
                source,
                ts.isoformat(timespec="seconds") + ("Z" if ts.tzinfo is None else ""),
                scrape_status,
                raw_note,
            ),
        )


def list_products() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM products ORDER BY brand, model"
        ).fetchall()
        return [dict(r) for r in rows]


def latest_price(product_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT * FROM price_snapshots
            WHERE product_id = ? AND scrape_status = 'ok'
            ORDER BY scraped_at DESC
            LIMIT 1
            """,
            (product_id,),
        ).fetchone()
        return dict(row) if row else None


def price_on_or_before(product_id: str, when: datetime) -> dict[str, Any] | None:
    cutoff = when.isoformat(timespec="seconds")
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT * FROM price_snapshots
            WHERE product_id = ?
              AND scrape_status = 'ok'
              AND scraped_at <= ?
            ORDER BY scraped_at DESC
            LIMIT 1
            """,
            (product_id, cutoff if cutoff.endswith("Z") else cutoff + "Z"),
        ).fetchone()
        return dict(row) if row else None


def price_history(product_id: str, days: int = 90) -> list[dict[str, Any]]:
    since = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds") + "Z"
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT scraped_at, price, currency, source, scrape_status
            FROM price_snapshots
            WHERE product_id = ? AND scraped_at >= ?
            ORDER BY scraped_at ASC
            """,
            (product_id, since),
        ).fetchall()
        return [dict(r) for r in rows]


def dashboard_rows(compare_days: int = 7) -> list[dict[str, Any]]:
    """Current price + compare-window price + delta for each product."""
    now = datetime.utcnow()
    compare_at = now - timedelta(days=compare_days)
    rows: list[dict[str, Any]] = []

    for product in list_products():
        current = latest_price(product["id"])
        past = price_on_or_before(product["id"], compare_at)

        current_price = current["price"] if current else None
        past_price = past["price"] if past else None
        delta = None
        delta_pct = None
        if current_price is not None and past_price is not None:
            delta = round(current_price - past_price, 2)
            if past_price != 0:
                delta_pct = round((delta / past_price) * 100, 2)

        rows.append(
            {
                **product,
                "current_price": current_price,
                "past_price": past_price,
                "delta": delta,
                "delta_pct": delta_pct,
                "compare_days": compare_days,
                "last_scraped": current["scraped_at"] if current else None,
                "currency": (current or {}).get("currency") or product.get("currency") or "USD",
                "price_source": current["source"] if current else None,
            }
        )
    return rows


def snapshot_count() -> int:
    with get_db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM price_snapshots").fetchone()[0])


def has_seed_data() -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM price_snapshots WHERE source = 'seed' LIMIT 1"
        ).fetchone()
        return row is not None
