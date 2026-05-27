#!/usr/bin/env python3
"""
Outscraper → GoHighLevel lead importer.

Searches Google Maps via Outscraper, then creates each business as a GHL contact
in the "New Lead" pipeline stage. Skips duplicates automatically (ghl_client handles upsert).

Usage:
  # Nationwide
  python tools/import_leads.py --query "roofing contractors" --limit 500

  # Specific area code only
  python tools/import_leads.py --query "HVAC companies" --area-code 864 --limit 1000

  # Specific location
  python tools/import_leads.py --query "plumbers" --location "Greenville, SC" --limit 250 --tag cold-list-may

Requirements:
  OUTSCRAPER_API_KEY in .env
  GHL_API_TOKEN, GHL_LOCATION_ID, GHL_PIPELINE_ID, GHL_STAGE_NEW_LEAD in .env
"""

import argparse
import os
import sys
import time

import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

sys.path.insert(0, os.path.dirname(__file__))
import ghl_client as ghl

load_dotenv()
console = Console()

OUTSCRAPER_API_KEY = os.getenv("OUTSCRAPER_API_KEY")
GHL_STAGE_NEW_LEAD = os.getenv("GHL_STAGE_NEW_LEAD")


def search_outscraper(query: str, limit: int, location: str | None = None) -> list[dict]:
    """Call Outscraper Google Maps Search API and return raw results."""
    if not OUTSCRAPER_API_KEY:
        console.print("[red]OUTSCRAPER_API_KEY not set in .env[/red]")
        sys.exit(1)

    full_query = f"{query} in {location}" if location else query
    console.print(f"[cyan]Searching Outscraper:[/cyan] {full_query} (limit {limit})")

    r = requests.get(
        "https://api.app.outscraper.com/maps/search-v3",
        headers={"X-API-KEY": OUTSCRAPER_API_KEY},
        params={
            "query": full_query,
            "limit": limit,
            "async": False,
            "fields": "name,full_address,phone,site,email,owner_title",
        },
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    results = data.get("data", [])
    # Outscraper may return nested lists
    if results and isinstance(results[0], list):
        results = [item for sublist in results for item in sublist]
    console.print(f"[green]Got {len(results)} results from Outscraper[/green]")
    return results


def parse_lead(row: dict) -> dict:
    """Extract the fields we care about from an Outscraper row."""
    name = row.get("name") or row.get("title") or ""
    phone = row.get("phone") or row.get("phone_1") or ""
    email = row.get("email") or row.get("email_1") or ""
    website = row.get("site") or row.get("website") or ""
    address = row.get("full_address") or row.get("address") or ""
    owner = row.get("owner_title") or ""
    return {
        "name": owner if owner else name,          # prefer owner name if available
        "company": name,
        "phone": phone.strip() if phone else None,
        "email": email.strip() if email else None,
        "website": website.strip() if website else None,
        "address": address.strip() if address else None,
    }


def import_to_ghl(leads: list[dict], tag: str | None) -> dict:
    """Push each lead into GHL. Returns summary counts."""
    counts = {"imported": 0, "skipped": 0, "errors": 0}

    tags = ["outscraper-import"]
    if tag:
        tags.append(tag)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Importing to GHL...", total=len(leads))

        for lead in leads:
            if not lead["name"] and not lead["phone"] and not lead["email"]:
                counts["skipped"] += 1
                progress.advance(task)
                continue

            try:
                contact = ghl.upsert_contact(
                    name=lead["name"] or lead["company"] or "Unknown",
                    phone=lead["phone"],
                    email=lead["email"],
                    tags=tags,
                )
                contact_id = contact.get("id") or contact.get("contactId")

                # Add website/address as a note if present
                note_parts = []
                if lead["company"] and lead["company"] != lead["name"]:
                    note_parts.append(f"Business: {lead['company']}")
                if lead["website"]:
                    note_parts.append(f"Website: {lead['website']}")
                if lead["address"]:
                    note_parts.append(f"Address: {lead['address']}")
                if note_parts:
                    ghl.add_note(contact_id, "\n".join(note_parts))

                # Move into pipeline at New Lead stage
                if GHL_STAGE_NEW_LEAD and contact_id:
                    ghl.upsert_opportunity(
                        contact_id=contact_id,
                        stage_id=GHL_STAGE_NEW_LEAD,
                        name=f"Deal — {lead['name'] or lead['company']}",
                    )

                counts["imported"] += 1
            except Exception as e:
                counts["errors"] += 1
                console.print(f"[yellow]Error on {lead.get('name', '?')}: {e}[/yellow]")

            progress.advance(task)
            time.sleep(0.12)  # stay under GHL rate limit (100 req/10s)

    return counts


def main():
    parser = argparse.ArgumentParser(description="Import Outscraper leads into GHL")
    parser.add_argument("--query", required=True, help='Search query e.g. "roofing contractors"')
    parser.add_argument("--location", default=None, help='Optional location e.g. "Greenville, SC"')
    parser.add_argument("--area-code", type=str, default=None, help="Filter results to a specific area code e.g. 864")
    parser.add_argument("--limit", type=int, default=500, help="Max leads to fetch (default 500)")
    parser.add_argument("--tag", default=None, help="Extra GHL tag to apply e.g. cold-list-may")
    args = parser.parse_args()

    raw = search_outscraper(args.query, args.limit, args.location)

    # Filter by area code if specified
    if args.area_code:
        before = len(raw)
        raw = [r for r in raw if str(r.get("phone") or r.get("phone_1") or "").lstrip("+1").startswith(args.area_code)]
        console.print(f"[cyan]Area code {args.area_code} filter:[/cyan] {before} → {len(raw)} leads")
    leads = [parse_lead(r) for r in raw]

    console.print(f"\n[bold]Preview — first 5 leads:[/bold]")
    t = Table(show_header=True, header_style="bold cyan")
    for col in ("Name", "Company", "Phone", "Email", "Website"):
        t.add_column(col)
    for lead in leads[:5]:
        t.add_row(
            lead["name"] or "", lead["company"] or "",
            lead["phone"] or "", lead["email"] or "", lead["website"] or "",
        )
    console.print(t)

    console.print(f"\nReady to import [bold]{len(leads)}[/bold] leads into GHL.")
    confirm = input("Proceed? [y/N] ").strip().lower()
    if confirm != "y":
        console.print("[yellow]Aborted.[/yellow]")
        sys.exit(0)

    counts = import_to_ghl(leads, args.tag)

    console.print(f"\n[bold green]Done![/bold green]")
    console.print(f"  Imported : {counts['imported']}")
    console.print(f"  Skipped  : {counts['skipped']} (no contact info)")
    console.print(f"  Errors   : {counts['errors']}")


if __name__ == "__main__":
    main()
