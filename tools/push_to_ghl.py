#!/usr/bin/env python3
"""
AB Growth Co — Push Leads CSV → GoHighLevel

Reads a leads CSV (from lead_scraper.py) and bulk-creates contacts in GHL,
setting the pipeline stage and tagging each contact.

Usage:
  python tools/push_to_ghl.py --csv .tmp/leads_roofing_fl.csv
  python tools/push_to_ghl.py --csv .tmp/leads_roofing_fl.csv --stage sent_to_dialer
"""

import argparse
import csv
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))
import ghl_client as ghl  # noqa: E402

console = Console()

STAGE_KEYS = {
    "new_lead":       os.getenv("GHL_STAGE_NEW_LEAD"),
    "sent_to_dialer": os.getenv("GHL_STAGE_SENT_TO_DIALER"),
}

TAG_MAP = {
    "new_lead":       "new-lead",
    "sent_to_dialer": "sent-to-dialer",
}


def push_csv(csv_path: str, stage_key: str) -> None:
    stage_id = STAGE_KEYS.get(stage_key)
    tag = TAG_MAP.get(stage_key, stage_key)

    if not stage_id:
        console.print(f"[yellow]Warning:[/yellow] No stage ID set for '{stage_key}' in .env — "
                      "contacts will be created without a pipeline stage.")

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    console.print(f"\n[bold]Pushing {len(rows)} leads → GHL[/bold] (stage={stage_key}, tag={tag})\n")

    table = Table("Business Name", "Phone", "Status", "Contact ID")
    created = skipped = errors = 0

    for row in rows:
        business = row.get("business_name", "").strip()
        owner    = row.get("owner_name", "").strip()
        phone    = row.get("phone", "").strip()
        city     = row.get("city", "").strip()
        state    = row.get("state", "").strip()
        niche    = row.get("niche", row.get("query", "")).strip()

        name = owner or business or "Unknown"

        try:
            contact = ghl.upsert_contact(
                name=name,
                phone=phone or None,
                tags=[tag],
            )
            contact_id = contact["id"]

            # Write business name and niche as a note (custom fields require known IDs)
            meta_parts = []
            if business:
                meta_parts.append(f"Business: {business}")
            if niche:
                meta_parts.append(f"Niche: {niche}")
            if city or state:
                meta_parts.append(f"Location: {city}, {state}".strip(", "))
            if meta_parts:
                ghl.add_note(contact_id, " | ".join(meta_parts))

            if stage_id:
                ghl.upsert_opportunity(
                    contact_id=contact_id,
                    stage_id=stage_id,
                    name=f"Deal — {business or name}",
                )

            table.add_row(business[:30] or name[:30], phone, "[green]OK[/green]", contact_id[:12] + "…")
            created += 1

        except ghl.GHLError as e:
            table.add_row(business[:30] or name[:30], phone, f"[red]ERR[/red]", str(e)[:30])
            errors += 1
        except Exception as e:
            table.add_row(business[:30] or name[:30], phone, f"[red]ERR[/red]", str(e)[:30])
            errors += 1

    console.print(table)
    console.print(
        f"\n[bold]Done.[/bold] Created/updated: {created} | Errors: {errors} | "
        f"Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
    )


def main():
    parser = argparse.ArgumentParser(description="Push leads CSV to GoHighLevel")
    parser.add_argument("--csv", required=True, help="Path to leads CSV file")
    parser.add_argument(
        "--stage",
        default="new_lead",
        choices=["new_lead", "sent_to_dialer"],
        help="Pipeline stage to assign (default: new_lead)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        console.print(f"[red]Error:[/red] CSV file not found: {args.csv}")
        sys.exit(1)

    push_csv(args.csv, args.stage)


if __name__ == "__main__":
    main()
