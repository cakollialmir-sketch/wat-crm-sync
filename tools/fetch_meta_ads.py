#!/usr/bin/env python3
"""
Check the Meta Ad Library for active ads from a target business.

Primary:  Meta Ad Library API (graph.facebook.com/ads_archive)
Fallback: Playwright headless scrape of facebook.com/ads/library

Usage:
  python tools/fetch_meta_ads.py \
    --business "Joe's Pizza" \
    --country US \
    --output .tmp/meta_ads.json
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

load_dotenv()

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
GRAPH_BASE = "https://graph.facebook.com/v19.0"


def _assess(count: int) -> str:
    if count == 0:
        return "no_ads"
    elif count <= 2:
        return "minimal"
    elif count <= 9:
        return "active"
    return "heavy"


# ---------------------------------------------------------------------------
# Meta Ad Library API
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type(requests.HTTPError),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(3),
)
def _ad_archive_page(params: dict) -> dict:
    r = requests.get(f"{GRAPH_BASE}/ads_archive", params=params, timeout=20)
    if r.status_code == 429:
        r.raise_for_status()
    r.raise_for_status()
    return r.json()


def fetch_via_meta_api(business: str, country: str) -> dict:
    if not META_ACCESS_TOKEN:
        raise RuntimeError("META_ACCESS_TOKEN not set")

    params = {
        "search_terms": business,
        "ad_reached_countries": f'["{country}"]',
        "ad_active_status": "ACTIVE",
        "search_type": "KEYWORD_UNORDERED",
        "fields": "id,ad_creation_time,ad_creative_bodies,page_name,impressions",
        "limit": 25,
        "access_token": META_ACCESS_TOKEN,
    }

    ads = []
    page = _ad_archive_page(params)
    ads.extend(page.get("data", []))

    # Follow pagination up to 2 more pages (50 ads max)
    for _ in range(2):
        next_url = page.get("paging", {}).get("next")
        if not next_url or len(ads) >= 50:
            break
        r = requests.get(next_url, timeout=20)
        r.raise_for_status()
        page = r.json()
        ads.extend(page.get("data", []))

    simplified = []
    for ad in ads[:50]:
        bodies = ad.get("ad_creative_bodies", [])
        simplified.append({
            "id": ad.get("id"),
            "page_name": ad.get("page_name"),
            "created": ad.get("ad_creation_time"),
            "body_snippet": bodies[0][:200] if bodies else "",
        })

    return {
        "business_name": business,
        "active_ad_count": len(simplified),
        "ads": simplified,
        "assessment": _assess(len(simplified)),
        "data_source": "meta_api",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Playwright fallback
# ---------------------------------------------------------------------------

def fetch_via_playwright(business: str, country: str) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("playwright not installed — run: pip install playwright && playwright install chromium")

    url = (
        f"https://www.facebook.com/ads/library/"
        f"?active_status=active&ad_type=all&country={country}"
        f"&q={requests.utils.quote(business)}&search_type=keyword_unordered"
    )

    ads = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # Grab ad card text — Facebook's DOM changes; use broad selectors
        cards = page.query_selector_all('[role="article"]')
        for card in cards[:50]:
            text = card.inner_text()
            if text.strip():
                ads.append({"body_snippet": text[:200]})

        browser.close()

    return {
        "business_name": business,
        "active_ad_count": len(ads),
        "ads": ads,
        "assessment": _assess(len(ads)),
        "data_source": "playwright_scrape",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Check Meta Ad Library for a business")
    parser.add_argument("--business", required=True)
    parser.add_argument("--country", default="US")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output) if os.path.dirname(args.output) else ".", exist_ok=True)

    result = None

    if META_ACCESS_TOKEN:
        try:
            print(f"Checking Meta Ad Library API: {args.business}")
            result = fetch_via_meta_api(args.business, args.country)
        except Exception as e:
            print(f"Meta API failed: {e}", file=sys.stderr)

    if result is None:
        try:
            print("Falling back to Playwright scrape of Ad Library...")
            result = fetch_via_playwright(args.business, args.country)
        except Exception as e:
            print(f"Playwright fallback also failed: {e}", file=sys.stderr)
            result = {
                "business_name": args.business,
                "active_ad_count": None,
                "ads": [],
                "assessment": "unknown",
                "error": str(e),
                "data_source": "failed",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(args.output, "w") as f:
                json.dump(result, f, indent=2)
            print(f"Warning: could not fetch Meta ad data. Saved partial result to {args.output}", file=sys.stderr)
            # Exit 0 — a failed ad check is non-fatal; audit can continue
            return

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Saved to {args.output}")
    print(f"  Active ads:  {result['active_ad_count']}")
    print(f"  Assessment:  {result['assessment']}")
    print(f"  Source:      {result['data_source']}")


if __name__ == "__main__":
    main()
