"""Price scrapers + seed history for demo/local use.

Live store pages change often and may block bots. The app always works offline
via seed data and manual price entry. Live scrapes are best-effort.
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .db import has_seed_data, insert_price, list_products, snapshot_count

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# Approximate street/list prices (USD) for seed + fallback demos
BASE_PRICES: dict[str, float] = {
    "mbp14-m4-pro": 1999.0,
    "mbp16-m4-pro": 2499.0,
    "mba13-m4": 1299.0,
    "x1c-gen13": 2149.0,
    "t14-gen6": 1599.0,
    "latitude-5550": 1349.0,
    "xps14-2025": 1899.0,
    "elitebook-840": 1549.0,
}


def _headers(url: str) -> dict[str, str]:
    host = urlparse(url).netloc
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://{host}/" if host else "https://www.google.com/",
    }


def _parse_money(text: str) -> float | None:
    if not text:
        return None
    cleaned = text.replace("\xa0", " ").strip()
    # Prefer amounts that look like product prices ($1,299.00 / 1299)
    matches = re.findall(r"(?:USD|US\$|\$)\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)", cleaned)
    if not matches:
        matches = re.findall(r"\b([0-9]{3,5}(?:\.[0-9]{2})?)\b", cleaned)
    candidates: list[float] = []
    for m in matches:
        try:
            val = float(m.replace(",", ""))
        except ValueError:
            continue
        if 200 <= val <= 10000:
            candidates.append(val)
    if not candidates:
        return None
    # Median-ish: middle of sorted unique
    uniq = sorted(set(candidates))
    return uniq[len(uniq) // 2]


def _extract_json_ld_price(soup: BeautifulSoup) -> float | None:
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            offers = item.get("offers")
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                price = offers.get("price") or offers.get("lowPrice")
                if price is not None:
                    try:
                        val = float(str(price).replace(",", ""))
                        if 200 <= val <= 10000:
                            return val
                    except ValueError:
                        pass
            # nested graph
            graph = item.get("@graph")
            if isinstance(graph, list):
                nested = BeautifulSoup("", "lxml")
                # recurse via json dump
                fake = soup.new_tag("script")
                fake.string = json.dumps(graph)
                nested.append(fake)
                found = _extract_json_ld_price(nested)
                if found:
                    return found
    return None


def _extract_meta_price(soup: BeautifulSoup) -> float | None:
    for prop in (
        "product:price:amount",
        "og:price:amount",
        "twitter:data1",
    ):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            val = _parse_money(str(tag["content"]))
            if val:
                return val
    return None


def extract_price_from_html(html: str, source: str = "generic") -> float | None:
    soup = BeautifulSoup(html, "lxml")

    # Structured data first
    for extractor in (_extract_json_ld_price, _extract_meta_price):
        val = extractor(soup)
        if val:
            return val

    # Apple often embeds price strings
    if source == "apple" or "apple.com" in html[:2000].lower():
        m = re.search(r'"price"\s*:\s*\{[^}]*"raw_amount"\s*:\s*"?([0-9.]+)"?', html)
        if m:
            try:
                val = float(m.group(1))
                if 200 <= val <= 10000:
                    return val
            except ValueError:
                pass
        m = re.search(r'"priceAmount"\s*:\s*"?([0-9.]+)"?', html)
        if m:
            try:
                val = float(m.group(1))
                if 200 <= val <= 10000:
                    return val
            except ValueError:
                pass

    # Common CSS hooks
    selectors = [
        "[itemprop=price]",
        ".price",
        ".product-price",
        ".price-current",
        "#priceblock_ourprice",
        ".a-price .a-offscreen",
        "[data-testid=price]",
        ".sales .value",
    ]
    for sel in selectors:
        node = soup.select_one(sel)
        if not node:
            continue
        content = node.get("content") or node.get_text(" ", strip=True)
        val = _parse_money(str(content))
        if val:
            return val

    # Last resort: scan visible text blobs
    text = soup.get_text(" ", strip=True)[:20000]
    return _parse_money(text)


async def fetch_price(product: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
    url = product.get("url")
    if not url:
        return {"ok": False, "error": "no url", "price": None}

    try:
        resp = await client.get(url, headers=_headers(url), follow_redirects=True)
        if resp.status_code >= 400:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "price": None}
        price = extract_price_from_html(resp.text, source=product.get("source") or "generic")
        if price is None:
            return {"ok": False, "error": "price not found in page", "price": None}
        return {"ok": True, "price": price, "error": None}
    except Exception as exc:  # noqa: BLE001 - surface scrape errors cleanly
        return {"ok": False, "error": str(exc), "price": None}


async def refresh_all_prices() -> list[dict[str, Any]]:
    products = list_products()
    results: list[dict[str, Any]] = []
    timeout = httpx.Timeout(25.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for product in products:
            result = await fetch_price(product, client)
            if result["ok"] and result["price"] is not None:
                insert_price(
                    product["id"],
                    result["price"],
                    currency=product.get("currency") or "USD",
                    source=product.get("source") or "scrape",
                    scrape_status="ok",
                )
                results.append(
                    {
                        "id": product["id"],
                        "model": product["model"],
                        "status": "ok",
                        "price": result["price"],
                    }
                )
            else:
                # Keep a failed attempt note so the UI can show scrape health
                insert_price(
                    product["id"],
                    BASE_PRICES.get(product["id"], 0.0) or 0.0,
                    currency=product.get("currency") or "USD",
                    source="scrape-failed",
                    scrape_status="failed",
                    raw_note=result.get("error"),
                )
                results.append(
                    {
                        "id": product["id"],
                        "model": product["model"],
                        "status": "failed",
                        "error": result.get("error"),
                        "price": None,
                    }
                )
    return results


def seed_history(days: int = 30, force: bool = False) -> int:
    """Insert synthetic daily history so week-over-week works immediately."""
    if snapshot_count() > 0 and not force and has_seed_data():
        return 0
    if snapshot_count() > 0 and not force:
        # Already has real data; don't clobber unless forced
        return 0

    products = list_products()
    if not products:
        return 0

    rng = random.Random(42)
    now = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    inserted = 0

    for product in products:
        base = BASE_PRICES.get(product["id"], 1500.0)
        # mild random walk so charts / WoW look realistic
        price = base * rng.uniform(0.97, 1.03)
        for day_offset in range(days, -1, -1):
            # occasional small sale / bump
            if rng.random() < 0.08:
                price *= rng.uniform(0.92, 0.97)
            else:
                price *= rng.uniform(0.995, 1.005)
            # mean reversion toward base
            price = price * 0.85 + base * 0.15
            ts = now - timedelta(days=day_offset)
            insert_price(
                product["id"],
                round(price, 2),
                currency=product.get("currency") or "USD",
                source="seed",
                scraped_at=ts,
                scrape_status="ok",
                raw_note="synthetic history for demo",
            )
            inserted += 1
    return inserted


def record_manual_price(product_id: str, price: float, currency: str = "USD") -> None:
    insert_price(
        product_id,
        price,
        currency=currency,
        source="manual",
        scrape_status="ok",
        raw_note="user-entered",
    )
