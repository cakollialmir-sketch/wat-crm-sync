#!/usr/bin/env python3
"""
Find top 3-5 local competitors in the same niche and collect their metrics:
  - Google rating, review count
  - Active Meta ad count

Primary:  Google Places API (New) — reuses GOOGLE_PLACES_API_KEY
Fallback: SerpAPI Google Maps

Usage:
  python tools/find_competitors.py \
    --business "Joe's Pizza" \
    --location "Austin, TX" \
    --reviews-file .tmp/google_reviews.json \   # optional — reads niche from Step 1 output
    --output .tmp/competitors.json

  # Override niche manually:
  python tools/find_competitors.py \
    --business "Joe's Pizza" \
    --location "Austin, TX" \
    --niche "pizza restaurant" \
    --output .tmp/competitors.json
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher

from typing import Optional

import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

load_dotenv()

PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
GRAPH_BASE = "https://graph.facebook.com/v19.0"

GENERIC_TYPES = {
    "establishment", "point_of_interest", "store", "food", "premise",
    "geocode", "political", "locality", "sublocality", "neighborhood",
}


def _name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _assess_ads(count) -> str:
    if count is None:
        return "unknown"
    if count == 0:
        return "no_ads"
    elif count <= 2:
        return "minimal"
    elif count <= 9:
        return "active"
    return "heavy"


# ---------------------------------------------------------------------------
# Quick Meta ad count for a competitor
# ---------------------------------------------------------------------------

def _quick_meta_ad_count(name: str, country: str = "US") -> Optional[int]:
    if not META_ACCESS_TOKEN:
        return None
    try:
        params = {
            "search_terms": name,
            "ad_reached_countries": f'["{country}"]',
            "ad_active_status": "ACTIVE",
            "search_type": "KEYWORD_UNORDERED",
            "fields": "id",
            "limit": 25,
            "access_token": META_ACCESS_TOKEN,
        }
        r = requests.get(f"{GRAPH_BASE}/ads_archive", params=params, timeout=15)
        if r.status_code == 429:
            time.sleep(5)
            return None
        r.raise_for_status()
        data = r.json()
        return len(data.get("data", []))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Google Places API
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type(requests.HTTPError),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
)
def _places_search_text(query: str, api_key: str, count: int = 10) -> dict:
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id,places.displayName,places.rating,places.userRatingCount,places.types,places.websiteUri,places.internationalPhoneNumber",
    }
    payload = {"textQuery": query, "maxResultCount": count}
    r = requests.post(url, headers=headers, json=payload, timeout=15)
    if r.status_code == 429:
        r.raise_for_status()
    r.raise_for_status()
    return r.json()


def fetch_competitors_via_google_places(business: str, location: str, niche: str) -> list[dict]:
    query = f"{niche} in {location}"
    print(f"  Searching Google Places: {query}")
    data = _places_search_text(query, PLACES_API_KEY, count=10)
    places = data.get("places", [])

    competitors = []
    for place in places:
        name = place.get("displayName", {}).get("text", "")
        # Skip the audited business itself
        if _name_similarity(name, business) > 0.6:
            continue

        all_types = place.get("types", [])
        specific_types = [t for t in all_types if t not in GENERIC_TYPES]
        primary_type = specific_types[0] if specific_types else (all_types[0] if all_types else "business")

        competitors.append({
            "name": name,
            "place_id": place.get("id", ""),
            "rating": place.get("rating"),
            "review_count": place.get("userRatingCount"),
            "primary_type": primary_type,
            "website": place.get("websiteUri", ""),
        })

        if len(competitors) >= 5:
            break

    # Sort by review count descending (most established first)
    competitors.sort(key=lambda x: x.get("review_count") or 0, reverse=True)
    return competitors[:5]


# ---------------------------------------------------------------------------
# SerpAPI fallback
# ---------------------------------------------------------------------------

def fetch_competitors_via_serpapi(business: str, location: str, niche: str) -> list[dict]:
    if not SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY not set")

    params = {
        "engine": "google_maps",
        "q": f"{niche} {location}",
        "api_key": SERPAPI_KEY,
        "type": "search",
    }
    r = requests.get("https://serpapi.com/search", params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    results = data.get("local_results", [])

    competitors = []
    for item in results:
        name = item.get("title", "")
        if _name_similarity(name, business) > 0.6:
            continue
        review_count = item.get("reviews", 0)
        if not isinstance(review_count, int):
            review_count = 0
        competitors.append({
            "name": name,
            "place_id": item.get("place_id", ""),
            "rating": item.get("rating"),
            "review_count": review_count,
            "primary_type": item.get("type", niche),
            "website": item.get("website", ""),
        })
        if len(competitors) >= 5:
            break

    competitors.sort(key=lambda x: x.get("review_count") or 0, reverse=True)
    return competitors[:5]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Find local competitors")
    parser.add_argument("--business", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--reviews-file", help="Path to google_reviews.json from Step 1")
    parser.add_argument("--niche", help="Override niche/business type for competitor search")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output) if os.path.dirname(args.output) else ".", exist_ok=True)

    # Determine niche
    niche = args.niche
    if not niche and args.reviews_file and os.path.exists(args.reviews_file):
        with open(args.reviews_file) as f:
            reviews_data = json.load(f)
        niche = reviews_data.get("primary_type", "")
        print(f"  Niche from reviews file: {niche}")

    if not niche:
        niche = args.business  # last resort: use business name as search term

    # Fetch competitors
    competitors = None
    error_msgs = []

    if PLACES_API_KEY:
        try:
            print(f"Fetching competitors via Google Places API...")
            competitors = fetch_competitors_via_google_places(args.business, args.location, niche)
        except Exception as e:
            print(f"Google Places failed: {e}", file=sys.stderr)
            error_msgs.append(str(e))

    if competitors is None or len(competitors) < 3:
        try:
            print(f"Fetching competitors via SerpAPI fallback...")
            competitors = fetch_competitors_via_serpapi(args.business, args.location, niche)
        except Exception as e:
            print(f"SerpAPI fallback failed: {e}", file=sys.stderr)
            error_msgs.append(str(e))
            if competitors is None:
                competitors = []

    # Enrich competitors with Meta ad data
    print(f"  Found {len(competitors)} competitors. Checking Meta ad activity...")
    for comp in competitors:
        count = _quick_meta_ad_count(comp["name"])
        comp["active_meta_ads"] = count
        comp["meta_ad_assessment"] = _assess_ads(count)
        time.sleep(0.5)  # gentle rate limiting

    result = {
        "competitors": competitors,
        "niche": niche,
        "location": args.location,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    if error_msgs:
        result["warnings"] = error_msgs

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Saved to {args.output}")
    for c in competitors:
        ads = c.get('active_meta_ads')
        ads_str = str(ads) if ads is not None else "unknown"
        print(f"  {c['name']}: {c.get('rating')}★ ({c.get('review_count')} reviews) | Meta ads: {ads_str}")


if __name__ == "__main__":
    main()
