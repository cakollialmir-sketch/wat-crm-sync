#!/usr/bin/env python3
"""
Greenville County SC roofing lead generation scraper for ABGrowthCo.

Collects 100 unique roofing businesses, enriches with Google/Facebook/Meta data,
scores by opportunity, and exports to a new Google Sheet with conditional formatting.

Usage:
  python tools/greenville_roofing_leads.py

Requirements:
  - SERPAPI_KEY in .env
  - GOOGLE_PLACES_API_KEY in .env (optional — uses SerpAPI fallback if missing)
  - ANTHROPIC_API_KEY in .env (optional — uses template notes if missing)
  - META_ACCESS_TOKEN in .env (optional — returns 'Unknown' if missing)
  - credentials.json in project root (Google Service Account for Sheets export)
"""

import csv
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SERPAPI_KEY = os.getenv("SERPAPI_KEY")
PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

GRAPH_BASE = "https://graph.facebook.com/v19.0"
TARGET_COUNT = 100
ERROR_LOG = ".tmp/errors_greenville_roofing.csv"

SEARCH_QUERIES = [
    "roofing company Greenville SC",
    "roofer Greenville SC",
    "roofing contractor Greenville SC",
    "roofing company Simpsonville SC",
    "roofing company Spartanburg SC",
    "roofing contractor Greenville County SC",
    "roof repair Greenville SC",
    "residential roofing Greenville SC",
]

console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _digits_only(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def dedup_key(lead: dict) -> tuple:
    name_slug = _slugify(lead.get("name", ""))[:30]
    phone_digits = _digits_only(lead.get("phone", ""))[-10:]
    return (name_slug, phone_digits)


def log_error(name: str, phone: str, error: str) -> None:
    with open(ERROR_LOG, "a", newline="") as f:
        csv.writer(f).writerow([datetime.now(timezone.utc).isoformat(), name, phone, error])


# ---------------------------------------------------------------------------
# Phase 1: Search — collect 100 unique leads via SerpAPI
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type(requests.HTTPError),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
)
def _serpapi_maps_request(params: dict) -> dict:
    r = requests.get("https://serpapi.com/search", params=params, timeout=20)
    if r.status_code == 429:
        r.raise_for_status()
    r.raise_for_status()
    return r.json()


def search_serpapi_local(query: str, start: int = 0) -> list[dict]:
    if not SERPAPI_KEY:
        return []
    params = {
        "engine": "google_maps",
        "q": query,
        "api_key": SERPAPI_KEY,
        "type": "search",
        "start": start,
    }
    try:
        data = _serpapi_maps_request(params)
    except Exception as e:
        log_error("search", "", f"SerpAPI search failed for '{query}' start={start}: {e}")
        return []

    results = data.get("local_results", [])
    leads = []
    for item in results:
        phone = item.get("phone", "")
        if not phone:
            continue
        review_count = item.get("reviews", 0)
        if not isinstance(review_count, int):
            review_count = 0
        leads.append({
            "name": item.get("title", ""),
            "phone": phone,
            "address": item.get("address", ""),
            "website": item.get("website", ""),
            "rating": item.get("rating"),
            "review_count": review_count,
            "place_id": item.get("place_id", ""),
        })
    return leads


def collect_leads() -> list[dict]:
    seen: set[tuple] = set()
    leads: list[dict] = []

    for query in SEARCH_QUERIES:
        if len(leads) >= TARGET_COUNT:
            break
        for start in [0, 20]:
            if len(leads) >= TARGET_COUNT:
                break
            console.print(f"  [dim]Searching: {query!r} (page {start // 20 + 1})[/]")
            results = search_serpapi_local(query, start)
            added = 0
            for r in results:
                if not r.get("phone"):
                    continue
                key = dedup_key(r)
                if key in seen:
                    continue
                seen.add(key)
                leads.append(r)
                added += 1
                if len(leads) >= TARGET_COUNT:
                    break
            console.print(f"    [dim]→ {added} new leads (total: {len(leads)})[/]")
            time.sleep(1.2)  # SerpAPI free tier: 1 req/sec

    if len(leads) < TARGET_COUNT:
        console.print(
            f"  [yellow]Warning: found {len(leads)} unique leads "
            f"(target was {TARGET_COUNT})[/]"
        )
    return leads


# ---------------------------------------------------------------------------
# Phase 2: Enrichment
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type(requests.HTTPError),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
)
def _places_search_text(query: str) -> dict:
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": PLACES_API_KEY,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.rating,"
            "places.userRatingCount,places.websiteUri"
        ),
    }
    payload = {"textQuery": query, "maxResultCount": 1}
    r = requests.post(url, headers=headers, json=payload, timeout=15)
    if r.status_code == 429:
        r.raise_for_status()
    r.raise_for_status()
    return r.json()


def enrich_google(name: str, address: str) -> dict:
    """Return enriched rating, review_count, and website. Google Places primary, SerpAPI fallback."""
    query = f"{name} {address}" if address else f"{name} Greenville SC"

    # Primary: Google Places API (New)
    if PLACES_API_KEY:
        try:
            data = _places_search_text(query)
            places = data.get("places", [])
            if places:
                p = places[0]
                return {
                    "rating": p.get("rating"),
                    "review_count": p.get("userRatingCount") or 0,
                    "website": p.get("websiteUri", ""),
                    "data_source": "google_places",
                }
        except Exception:
            pass

    # Fallback: SerpAPI
    if SERPAPI_KEY:
        try:
            params = {
                "engine": "google_maps",
                "q": query,
                "api_key": SERPAPI_KEY,
                "type": "search",
            }
            r = requests.get("https://serpapi.com/search", params=params, timeout=20)
            r.raise_for_status()
            results = r.json().get("local_results", [])
            if results:
                top = results[0]
                review_count = top.get("reviews", 0)
                if not isinstance(review_count, int):
                    review_count = 0
                return {
                    "rating": top.get("rating"),
                    "review_count": review_count,
                    "website": top.get("website", ""),
                    "data_source": "serpapi",
                }
        except Exception:
            pass

    return {"rating": None, "review_count": 0, "website": "", "data_source": "none"}


def enrich_meta_ads(name: str) -> str:
    """Return 'Yes', 'No', or 'Unknown' for active Meta ads."""
    if not META_ACCESS_TOKEN:
        return "Unknown"
    try:
        params = {
            "search_terms": name,
            "ad_reached_countries": '["US"]',
            "ad_active_status": "ACTIVE",
            "search_type": "KEYWORD_UNORDERED",
            "fields": "id",
            "limit": 25,
            "access_token": META_ACCESS_TOKEN,
        }
        r = requests.get(f"{GRAPH_BASE}/ads_archive", params=params, timeout=15)
        if r.status_code == 429:
            time.sleep(5)
            return "Unknown"
        r.raise_for_status()
        count = len(r.json().get("data", []))
        return "Yes" if count > 0 else "No"
    except Exception:
        return "Unknown"


def enrich_facebook(name: str) -> str:
    """Return 'Yes', 'No', or 'Unknown' for Facebook page existence."""
    if not META_ACCESS_TOKEN:
        return "Unknown"
    try:
        params = {
            "q": name,
            "type": "page",
            "fields": "id,name",
            "access_token": META_ACCESS_TOKEN,
        }
        r = requests.get(f"{GRAPH_BASE}/search", params=params, timeout=15)
        if r.status_code == 429:
            time.sleep(5)
            return "Unknown"
        r.raise_for_status()
        results = r.json().get("data", [])
        return "Yes" if results else "No"
    except Exception:
        return "Unknown"


def _fallback_note(lead: dict) -> str:
    """Template-based note when Claude API is unavailable."""
    issues = []
    if not lead.get("website"):
        issues.append("no website")
    review_count = lead.get("review_count", 0) or 0
    if review_count < 10:
        issues.append(f"only {review_count} Google reviews")
    if lead.get("meta_ads") == "No":
        issues.append("no paid advertising")
    if lead.get("facebook_page") == "No":
        issues.append("no Facebook presence")
    if issues:
        return f"{lead.get('name', 'Business')} has {', '.join(issues)} — strong pitch opportunity for ABGrowthCo."
    return f"{lead.get('name', 'Business')} has limited digital marketing gaps — lower priority lead."


def generate_note(lead: dict) -> str:
    """Generate a one-sentence Claude observation. Falls back to template if API key missing."""
    if not ANTHROPIC_API_KEY:
        return _fallback_note(lead)
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt = (
            f"Business: {lead.get('name', '')}\n"
            f"Google rating: {lead.get('rating', 'N/A')} ({lead.get('review_count', 0)} reviews)\n"
            f"Website: {'none' if not lead.get('website') else lead['website']}\n"
            f"Facebook page: {lead.get('facebook_page', 'Unknown')}\n"
            f"Meta ads running: {lead.get('meta_ads', 'Unknown')}\n\n"
            "Write exactly ONE sentence (under 20 words) identifying the single biggest "
            "digital marketing weakness this roofing company has. Be specific and direct."
        )
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception:
        return _fallback_note(lead)


# ---------------------------------------------------------------------------
# Phase 3: Priority scoring
# ---------------------------------------------------------------------------

def calculate_priority(lead: dict) -> int:
    score = 0
    review_count = lead.get("review_count", 0) or 0
    if lead.get("meta_ads") == "No":
        score += 3
    if review_count < 10:
        score += 2
    if not lead.get("website"):
        score += 2
    if lead.get("rating") and lead["rating"] < 4.0:
        score += 1
    if lead.get("facebook_page") == "No":
        score += 1
    if review_count < 25:
        score += 1  # cumulative with <10 check above
    return min(score, 10)


# ---------------------------------------------------------------------------
# Phase 4: Google Sheets export
# ---------------------------------------------------------------------------

def get_gspread_client():
    """Verbatim from push_to_sheets.py — uses credentials.json service account."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        raise RuntimeError("Missing dependencies. Run: pip install gspread google-auth")

    creds_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "credentials.json")
    if not os.path.exists(creds_path):
        raise FileNotFoundError(
            f"credentials.json not found at {creds_path}.\n"
            "To set up:\n"
            "  1. console.cloud.google.com → APIs & Services → Credentials\n"
            "  2. Create Service Account → download JSON key → save as credentials.json in project root\n"
            "  3. Enable Google Sheets API and Google Drive API for the project"
        )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    return gspread.authorize(creds)


def _apply_formatting(spreadsheet, worksheet, row_count: int) -> None:
    """Apply conditional formatting via raw Sheets API v4 batch_update."""
    ws_id = worksheet.id  # integer grid sheet ID (0 for first sheet)

    requests_body = [
        # Rule 1: Green entire row when Priority Score (col A) >= 8
        # Requires CUSTOM_FORMULA because the range spans multiple columns
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": ws_id,
                        "startRowIndex": 1,
                        "endRowIndex": row_count + 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 12,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{"userEnteredValue": "=$A2>=8"}],
                        },
                        "format": {
                            "backgroundColor": {"red": 0.57, "green": 0.82, "blue": 0.57}
                        },
                    },
                },
                "index": 0,
            }
        },
        # Rule 2: Orange Review Count cell (col G = index 6) when < 10
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": ws_id,
                        "startRowIndex": 1,
                        "endRowIndex": row_count + 1,
                        "startColumnIndex": 6,
                        "endColumnIndex": 7,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "NUMBER_LESS",
                            "values": [{"userEnteredValue": "10"}],
                        },
                        "format": {
                            "backgroundColor": {"red": 1.0, "green": 0.60, "blue": 0.0}
                        },
                    },
                },
                "index": 1,
            }
        },
        # Rule 3: Red Website cell (col E = index 4) when text equals "NO WEBSITE"
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": ws_id,
                        "startRowIndex": 1,
                        "endRowIndex": row_count + 1,
                        "startColumnIndex": 4,
                        "endColumnIndex": 5,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "TEXT_EQ",
                            "values": [{"userEnteredValue": "NO WEBSITE"}],
                        },
                        "format": {
                            "backgroundColor": {"red": 0.90, "green": 0.30, "blue": 0.30}
                        },
                    },
                },
                "index": 2,
            }
        },
    ]
    spreadsheet.batch_update({"requests": requests_body})


FALLBACK_SHEET_ID = "1KatZ0oK2XzqfZCTmsunT6bjkFB8sdQrTT_6_SA3-zEg"


def create_sheet(leads: list[dict]) -> str:
    """Create a new Google Sheet (or reuse existing if Drive quota exceeded), populate it, apply formatting."""
    client = get_gspread_client()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = f"Greenville Roofing Leads - {today}"

    spreadsheet = None
    try:
        spreadsheet = client.create(title)
    except Exception as create_err:
        if "quota" in str(create_err).lower() or "403" in str(create_err):
            console.print(
                f"  [yellow]Drive create quota hit — reusing existing sheet ({FALLBACK_SHEET_ID})[/]"
            )
            spreadsheet = client.open_by_key(FALLBACK_SHEET_ID)
            spreadsheet.update_title(title)
        else:
            raise

    worksheet = spreadsheet.get_worksheet(0)
    worksheet.clear()

    headers = [
        "Priority Score", "Business Name", "Phone Number", "Physical Address",
        "Website", "Google Rating", "Review Count", "Facebook Page",
        "Meta Ads Running", "Notes", "Status", "Date Scraped",
    ]
    worksheet.append_row(headers, value_input_option="RAW")

    # Sort: Priority Score descending, then Review Count ascending
    sorted_leads = sorted(
        leads,
        key=lambda x: (-x.get("priority", 0), x.get("review_count", 0) or 0)
    )

    rows = []
    for lead in sorted_leads:
        rows.append([
            lead.get("priority", 0),
            lead.get("name", ""),
            lead.get("phone", ""),
            lead.get("address", ""),
            lead.get("website") or "NO WEBSITE",
            lead.get("rating") if lead.get("rating") is not None else "",
            lead.get("review_count", 0) or 0,
            lead.get("facebook_page", "Unknown"),
            lead.get("meta_ads", "Unknown"),
            lead.get("note", ""),
            "",      # Status — blank for manual update during calls
            today,
        ])

    # Batch write all rows in one API call
    worksheet.append_rows(rows, value_input_option="USER_ENTERED")

    # Apply conditional formatting
    _apply_formatting(spreadsheet, worksheet, len(rows))

    # Make accessible via share link (view-only)
    spreadsheet.share(None, perm_type="anyone", role="reader")

    return spreadsheet.url


def _save_csv_fallback(leads: list[dict], path: str) -> None:
    """Emergency CSV fallback if Google Sheets export fails."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sorted_leads = sorted(
        leads,
        key=lambda x: (-x.get("priority", 0), x.get("review_count", 0) or 0)
    )
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "priority", "name", "phone", "address", "website",
            "rating", "review_count", "facebook_page", "meta_ads",
            "note", "status", "date_scraped",
        ])
        writer.writeheader()
        for lead in sorted_leads:
            writer.writerow({
                "priority": lead.get("priority", 0),
                "name": lead.get("name", ""),
                "phone": lead.get("phone", ""),
                "address": lead.get("address", ""),
                "website": lead.get("website") or "NO WEBSITE",
                "rating": lead.get("rating", ""),
                "review_count": lead.get("review_count", 0) or 0,
                "facebook_page": lead.get("facebook_page", "Unknown"),
                "meta_ads": lead.get("meta_ads", "Unknown"),
                "note": lead.get("note", ""),
                "status": "",
                "date_scraped": today,
            })


def print_summary(leads: list[dict], sheet_url: str) -> None:
    total = len(leads)
    no_website = sum(1 for lead in leads if not lead.get("website"))
    no_ads = sum(1 for lead in leads if lead.get("meta_ads") == "No")
    high_priority = sum(1 for lead in leads if lead.get("priority", 0) >= 8)

    console.print()
    console.print("[bold green]── RESULTS ───────────────────────────────[/]")
    console.print(f"  Total leads found:         [bold]{total}[/]")
    console.print(f"  No website:                [bold yellow]{no_website}[/]")
    console.print(f"  No Meta ads:               [bold yellow]{no_ads}[/]")
    console.print(f"  Priority 8-10 (hot leads): [bold green]{high_priority}[/]")
    console.print(f"  Google Sheet:              [bold blue]{sheet_url}[/]")
    console.print("[bold green]──────────────────────────────────────────[/]")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def main() -> None:
    os.makedirs(".tmp", exist_ok=True)

    # Validate required keys
    if not SERPAPI_KEY:
        console.print("[bold red]Error: SERPAPI_KEY not set in .env — required for search[/]")
        sys.exit(1)

    if not META_ACCESS_TOKEN:
        console.print(
            "[yellow]Note: META_ACCESS_TOKEN not set — "
            "Meta Ads and Facebook fields will show 'Unknown'[/]"
        )
    if not ANTHROPIC_API_KEY:
        console.print(
            "[yellow]Note: ANTHROPIC_API_KEY not set — "
            "using template-generated notes[/]"
        )
    if not PLACES_API_KEY:
        console.print(
            "[yellow]Note: GOOGLE_PLACES_API_KEY not set — "
            "using SerpAPI for review enrichment[/]"
        )

    # Phase 1: Search
    console.print("\n[bold]Phase 1: Searching for roofing companies in Greenville County SC...[/]")
    leads = collect_leads()
    console.print(f"\n  [green]Collected {len(leads)} unique leads with phone numbers[/]")

    if not leads:
        console.print("[bold red]No leads found. Check SERPAPI_KEY and try again.[/]")
        sys.exit(1)

    # Phase 2 + 3: Enrich and score
    console.print("\n[bold]Phase 2: Enriching leads with business data...[/]")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Enriching leads", total=len(leads))

        for lead in leads:
            try:
                # Google enrichment — more accurate rating/review/website than search results
                enriched = enrich_google(lead["name"], lead.get("address", ""))
                if enriched.get("rating") is not None:
                    lead["rating"] = enriched["rating"]
                if (enriched.get("review_count") or 0) > (lead.get("review_count") or 0):
                    lead["review_count"] = enriched["review_count"]
                # Prefer Places API website (more reliable than Maps snippet)
                if enriched.get("website"):
                    lead["website"] = enriched["website"]

                # Meta ads check — sleep 0.5s to respect shared Graph API quota
                lead["meta_ads"] = enrich_meta_ads(lead["name"])
                time.sleep(0.5)

                # Facebook presence check — shares Meta API quota
                lead["facebook_page"] = enrich_facebook(lead["name"])
                time.sleep(0.5)

                # Score
                lead["priority"] = calculate_priority(lead)

                # Claude note — sleep 1.2s to stay under 50 RPM
                lead["note"] = generate_note(lead)
                if ANTHROPIC_API_KEY:
                    time.sleep(1.2)

            except Exception as e:
                log_error(lead.get("name", ""), lead.get("phone", ""), str(e))
                lead.setdefault("meta_ads", "Unknown")
                lead.setdefault("facebook_page", "Unknown")
                lead["priority"] = calculate_priority(lead)
                lead.setdefault("note", _fallback_note(lead))

            progress.advance(task)

    # Phase 4: Export
    console.print("\n[bold]Phase 3: Exporting to Google Sheets...[/]")
    try:
        sheet_url = create_sheet(leads)
    except FileNotFoundError as e:
        console.print(f"\n[bold red]Google Sheets setup error:[/]\n{e}")
        fallback_path = ".tmp/greenville_roofing_leads_fallback.csv"
        _save_csv_fallback(leads, fallback_path)
        console.print(f"\n[yellow]Data saved to CSV fallback: {fallback_path}[/]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[bold red]Failed to create Google Sheet: {e}[/]")
        fallback_path = ".tmp/greenville_roofing_leads_fallback.csv"
        _save_csv_fallback(leads, fallback_path)
        console.print(f"[yellow]Data saved to CSV fallback: {fallback_path}[/]")
        sys.exit(1)

    print_summary(leads, sheet_url)


if __name__ == "__main__":
    main()
