#!/usr/bin/env python3
"""
Analyze a business's Facebook Page and Instagram profile:
  - Follower count, last post date, engagement rate
  - Presence quality classification

Primary:  Meta Graph API
Fallback: Apify facebook-pages-scraper actor

Usage:
  python tools/fetch_social_presence.py \
    --business "Joe's Pizza" \
    --location "Austin, TX" \
    --output .tmp/social_presence.json
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher

import requests
from dotenv import load_dotenv

load_dotenv()

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN")
GRAPH_BASE = "https://graph.facebook.com/v19.0"


def _name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _classify_presence(follower_count, last_post_days_ago, engagement_rate_pct) -> str:
    if follower_count is None:
        return "no_presence"
    if last_post_days_ago is None or last_post_days_ago > 90:
        return "ghost"
    if last_post_days_ago > 30:
        return "inactive"
    if follower_count >= 1000 and engagement_rate_pct is not None and engagement_rate_pct >= 3.0:
        return "strong"
    if follower_count >= 1000:
        return "active"
    return "moderate"


def _overall_score(fb: dict, ig: dict) -> int:
    """Score 1-5 based on combined presence."""
    score = 1
    fb_quality = fb.get("presence_quality", "no_presence")
    ig_found = ig.get("found", False)

    quality_map = {"no_presence": 0, "ghost": 1, "inactive": 2, "moderate": 3, "active": 4, "strong": 5}
    fb_score = quality_map.get(fb_quality, 0)

    if fb_score >= 4 and ig_found:
        score = 5
    elif fb_score >= 4:
        score = 4
    elif fb_score >= 3:
        score = 3
    elif fb_score >= 2:
        score = 2
    else:
        score = 1
    return score


# ---------------------------------------------------------------------------
# Meta Graph API
# ---------------------------------------------------------------------------

def _graph_get(path: str, params: dict) -> dict:
    params["access_token"] = META_ACCESS_TOKEN
    r = requests.get(f"{GRAPH_BASE}{path}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_via_meta_graph(business: str, location: str) -> dict:
    if not META_ACCESS_TOKEN:
        raise RuntimeError("META_ACCESS_TOKEN not set")

    # Search for Facebook Page
    search_query = f"{business} {location}"
    search_data = _graph_get("/pages/search", {
        "q": search_query,
        "fields": "id,name,fan_count,link,category",
        "limit": 5,
    })

    pages = search_data.get("data", [])
    if not pages:
        return {
            "facebook": {"found": False, "reason": "page_not_found_in_search"},
            "instagram": {"found": False, "reason": "no_facebook_page"},
            "overall_social_score": 1,
            "data_source": "meta_graph_api",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    # Pick best match by name similarity
    best_page = max(pages, key=lambda p: _name_similarity(p.get("name", ""), business))
    page_id = best_page["id"]
    fan_count = best_page.get("fan_count")

    # Fetch recent posts
    posts_data = _graph_get(f"/{page_id}/posts", {
        "fields": "created_time,message,likes.summary(true),comments.summary(true)",
        "limit": 10,
    })
    posts = posts_data.get("data", [])

    last_post_days_ago = None
    avg_likes = None
    avg_comments = None
    engagement_rate_pct = None

    if posts:
        latest_time = datetime.fromisoformat(posts[0]["created_time"].replace("Z", "+00:00"))
        last_post_days_ago = (datetime.now(timezone.utc) - latest_time).days

        likes_list = [p.get("likes", {}).get("summary", {}).get("total_count", 0) for p in posts]
        comments_list = [p.get("comments", {}).get("summary", {}).get("total_count", 0) for p in posts]
        avg_likes = sum(likes_list) / len(likes_list)
        avg_comments = sum(comments_list) / len(comments_list)

        if fan_count and fan_count > 0:
            engagement_rate_pct = round((avg_likes + avg_comments) / fan_count * 100, 2)

    presence_quality = _classify_presence(fan_count, last_post_days_ago, engagement_rate_pct)

    fb_result = {
        "found": True,
        "page_id": page_id,
        "page_name": best_page.get("name"),
        "page_url": best_page.get("link"),
        "follower_count": fan_count,
        "last_post_days_ago": last_post_days_ago,
        "avg_likes_per_post": round(avg_likes, 1) if avg_likes is not None else None,
        "avg_comments_per_post": round(avg_comments, 1) if avg_comments is not None else None,
        "engagement_rate_pct": engagement_rate_pct,
        "presence_quality": presence_quality,
    }

    # Check for linked Instagram Business Account
    ig_result = {"found": False, "reason": "no_linked_account"}
    try:
        ig_link_data = _graph_get(f"/{page_id}", {"fields": "instagram_business_account"})
        ig_account = ig_link_data.get("instagram_business_account")
        if ig_account:
            ig_id = ig_account["id"]
            ig_profile = _graph_get(f"/{ig_id}", {
                "fields": "username,followers_count,media_count,biography"
            })

            # Fetch recent media for engagement
            ig_media = _graph_get(f"/{ig_id}/media", {
                "fields": "timestamp,like_count,comments_count",
                "limit": 12,
            })
            media_items = ig_media.get("data", [])
            ig_avg_likes = None
            ig_avg_comments = None
            ig_engagement = None
            ig_last_post_days_ago = None

            if media_items:
                latest_ig_time = datetime.fromisoformat(media_items[0]["timestamp"].replace("Z", "+00:00"))
                ig_last_post_days_ago = (datetime.now(timezone.utc) - latest_ig_time).days
                ig_likes = [m.get("like_count", 0) for m in media_items]
                ig_cmts = [m.get("comments_count", 0) for m in media_items]
                ig_avg_likes = round(sum(ig_likes) / len(ig_likes), 1)
                ig_avg_comments = round(sum(ig_cmts) / len(ig_cmts), 1)
                ig_followers = ig_profile.get("followers_count", 0)
                if ig_followers and ig_followers > 0:
                    ig_engagement = round((ig_avg_likes + ig_avg_comments) / ig_followers * 100, 2)

            ig_result = {
                "found": True,
                "ig_id": ig_id,
                "username": ig_profile.get("username"),
                "followers_count": ig_profile.get("followers_count"),
                "media_count": ig_profile.get("media_count"),
                "last_post_days_ago": ig_last_post_days_ago,
                "avg_likes_per_post": ig_avg_likes,
                "avg_comments_per_post": ig_avg_comments,
                "engagement_rate_pct": ig_engagement,
            }
    except Exception as e:
        ig_result = {"found": False, "reason": f"api_error: {e}"}

    overall = _overall_score(fb_result, ig_result)

    return {
        "facebook": fb_result,
        "instagram": ig_result,
        "overall_social_score": overall,
        "data_source": "meta_graph_api",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Apify fallback
# ---------------------------------------------------------------------------

def fetch_via_apify(business: str, location: str) -> dict:
    if not APIFY_API_TOKEN:
        raise RuntimeError("APIFY_API_TOKEN not set")

    search_query = f"{business} {location}"
    run_url = "https://api.apify.com/v2/acts/apify~facebook-pages-scraper/runs"
    headers = {"Content-Type": "application/json"}
    params = {"token": APIFY_API_TOKEN}
    payload = {
        "startUrls": [],
        "searchTerms": [search_query],
        "maxPagesPerSearch": 1,
    }
    r = requests.post(run_url, headers=headers, params=params, json=payload, timeout=30)
    r.raise_for_status()
    run_id = r.json()["data"]["id"]

    # Poll for completion (max 60s)
    print(f"  Apify run started ({run_id}). Polling for result...")
    for _ in range(12):
        time.sleep(5)
        status_r = requests.get(
            f"https://api.apify.com/v2/actor-runs/{run_id}",
            params={"token": APIFY_API_TOKEN},
            timeout=15,
        )
        status_r.raise_for_status()
        status = status_r.json()["data"]["status"]
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {status}")
    else:
        raise RuntimeError("Apify run timed out after 60s")

    # Fetch dataset items
    dataset_id = status_r.json()["data"]["defaultDatasetId"]
    items_r = requests.get(
        f"https://api.apify.com/v2/datasets/{dataset_id}/items",
        params={"token": APIFY_API_TOKEN, "format": "json"},
        timeout=15,
    )
    items_r.raise_for_status()
    items = items_r.json()

    if not items:
        return {
            "facebook": {"found": False, "reason": "apify_no_results"},
            "instagram": {"found": False, "reason": "no_facebook_page"},
            "overall_social_score": 1,
            "data_source": "apify",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    item = items[0]
    fan_count = item.get("likes") or item.get("followers")
    last_post_days_ago = None
    if item.get("latestPostDate"):
        try:
            latest = datetime.fromisoformat(item["latestPostDate"])
            last_post_days_ago = (datetime.now(timezone.utc) - latest.replace(tzinfo=timezone.utc)).days
        except Exception:
            pass

    presence_quality = _classify_presence(fan_count, last_post_days_ago, None)

    fb_result = {
        "found": True,
        "page_name": item.get("title"),
        "page_url": item.get("url"),
        "follower_count": fan_count,
        "last_post_days_ago": last_post_days_ago,
        "avg_likes_per_post": None,
        "avg_comments_per_post": None,
        "engagement_rate_pct": None,
        "presence_quality": presence_quality,
    }

    return {
        "facebook": fb_result,
        "instagram": {"found": False, "reason": "apify_does_not_provide_ig"},
        "overall_social_score": _overall_score(fb_result, {"found": False}),
        "data_source": "apify",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Analyze social media presence")
    parser.add_argument("--business", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output) if os.path.dirname(args.output) else ".", exist_ok=True)

    result = None

    if META_ACCESS_TOKEN:
        try:
            print(f"Fetching social presence via Meta Graph API: {args.business}")
            result = fetch_via_meta_graph(args.business, args.location)
        except Exception as e:
            print(f"Meta Graph API failed: {e}", file=sys.stderr)

    if result is None:
        try:
            print("Falling back to Apify...")
            result = fetch_via_apify(args.business, args.location)
        except Exception as e:
            print(f"Apify fallback also failed: {e}", file=sys.stderr)
            result = {
                "facebook": {"found": False, "reason": f"all_sources_failed: {e}"},
                "instagram": {"found": False, "reason": "all_sources_failed"},
                "overall_social_score": 1,
                "data_source": "failed",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Saved to {args.output}")
    fb = result.get("facebook", {})
    ig = result.get("instagram", {})
    if fb.get("found"):
        print(f"  Facebook: {fb.get('follower_count')} followers | {fb.get('presence_quality')} | last post {fb.get('last_post_days_ago')}d ago")
    else:
        print(f"  Facebook: not found ({fb.get('reason')})")
    if ig.get("found"):
        print(f"  Instagram: @{ig.get('username')} | {ig.get('followers_count')} followers")
    else:
        print(f"  Instagram: not found")
    print(f"  Social score: {result.get('overall_social_score')}/5")


if __name__ == "__main__":
    main()
