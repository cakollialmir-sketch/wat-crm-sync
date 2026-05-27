#!/usr/bin/env python3
"""
Fetch Google Places data for a target business:
  - Star rating, review count, business type
  - Up to 5 recent review texts

Primary:  Google Places API (New)
Fallback: SerpAPI Google Maps

Usage:
  python tools/fetch_google_reviews.py \
    --business "Joe's Pizza" \
    --location "Austin, TX" \
    --output .tmp/google_reviews.json
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

PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

GENERIC_TYPES = {
    "establishment", "point_of_interest", "store", "food", "premise",
    "geocode", "political", "locality", "sublocality", "neighborhood",
}


# ---------------------------------------------------------------------------
# Google Places API (New)
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type(requests.HTTPError),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
)
def _places_search_text(query: str, api_key: str) -> dict:
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id,places.displayName,places.rating,places.userRatingCount,places.types",
    }
    payload = {"textQuery": query, "maxResultCount": 1}
    r = requests.post(url, headers=headers, json=payload, timeout=15)
    if r.status_code == 429:
        r.raise_for_status()
    r.raise_for_status()
    return r.json()


@retry(
    retry=retry_if_exception_type(requests.HTTPError),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
)
def _places_detail(place_id: str, api_key: str) -> dict:
    url = f"https://places.googleapis.com/v1/{place_id}"
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "reviews,rating,userRatingCount,displayName,types",
    }
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code == 429:
        r.raise_for_status()
    r.raise_for_status()
    return r.json()


def fetch_via_google_places(business: str, location: str) -> dict:
    query = f"{business} {location}"
    search_result = _places_search_text(query, PLACES_API_KEY)

    places = search_result.get("places", [])
    if not places:
        raise ValueError(f"No Google Places results for: {query}")

    place = places[0]
    place_id = place["id"]
    detail = _places_detail(place_id, PLACES_API_KEY)

    all_types = detail.get("types", place.get("types", []))
    specific_types = [t for t in all_types if t not in GENERIC_TYPES]
    primary_type = specific_types[0] if specific_types else (all_types[0] if all_types else "business")

    reviews = []
    for r in detail.get("reviews", []):
        reviews.append({
            "rating": r.get("rating"),
            "text": r.get("text", {}).get("text", ""),
            "author": r.get("authorAttribution", {}).get("displayName", ""),
            "time": r.get("publishTime", ""),
        })

    return {
        "place_id": place_id,
        "name": detail.get("displayName", {}).get("text", business),
        "rating": detail.get("rating", place.get("rating")),
        "review_count": detail.get("userRatingCount", place.get("userRatingCount")),
        "primary_type": primary_type,
        "all_types": all_types,
        "reviews": reviews,
        "data_source": "google_places_api",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# SerpAPI fallback
# ---------------------------------------------------------------------------

def fetch_via_serpapi(business: str, location: str) -> dict:
    if not SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY not set — cannot use fallback")

    params = {
        "engine": "google_maps",
        "q": f"{business} {location}",
        "api_key": SERPAPI_KEY,
        "type": "search",
    }
    r = requests.get("https://serpapi.com/search", params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    results = data.get("local_results", [])
    if not results:
        raise ValueError(f"No SerpAPI results for: {business} {location}")

    top = results[0]

    # SerpAPI returns `reviews` as an integer count, not a list
    review_count = top.get("reviews", 0)
    if not isinstance(review_count, int):
        review_count = 0

    # `user_review` may contain one sample review text
    reviews = []
    user_review = top.get("user_review")
    if isinstance(user_review, dict):
        reviews.append({
            "rating": user_review.get("rating"),
            "text": user_review.get("snippet", ""),
            "author": "Recent customer",
            "time": "",
        })

    types = top.get("types", [top.get("type", "business")])
    primary_type = types[0] if types else "business"

    return {
        "place_id": top.get("place_id", ""),
        "name": top.get("title", business),
        "rating": top.get("rating"),
        "review_count": review_count,
        "primary_type": primary_type,
        "all_types": types,
        "reviews": reviews,
        "data_source": "serpapi",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fetch Google reviews for a business")
    parser.add_argument("--business", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output) if os.path.dirname(args.output) else ".", exist_ok=True)

    result = None
    error_msg = None

    if PLACES_API_KEY:
        try:
            print(f"Fetching via Google Places API: {args.business}, {args.location}")
            result = fetch_via_google_places(args.business, args.location)
        except Exception as e:
            print(f"Google Places API failed: {e}", file=sys.stderr)
            error_msg = str(e)

    if result is None:
        try:
            print(f"Falling back to SerpAPI: {args.business}, {args.location}")
            result = fetch_via_serpapi(args.business, args.location)
        except Exception as e:
            print(f"SerpAPI fallback also failed: {e}", file=sys.stderr)
            result = {
                "error": "both_sources_failed",
                "primary_error": error_msg,
                "fallback_error": str(e),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(args.output, "w") as f:
                json.dump(result, f, indent=2)
            sys.exit(1)

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Saved to {args.output}")
    print(f"  Rating: {result.get('rating')} ({result.get('review_count')} reviews)")
    print(f"  Type:   {result.get('primary_type')}")
    print(f"  Source: {result.get('data_source')}")


if __name__ == "__main__":
    main()
