#!/usr/bin/env python3
"""
Manually log a call outcome to GoHighLevel when using a personal phone.
Use this when your power dialer didn't fire a webhook (manual cell calls, etc.).

Usage examples:
  python tools/log_call.py --name "John Smith" --phone "+15551234567" --disposition no_answer

  python tools/log_call.py \\
    --name "Sara Jones" --phone "+15559876543" \\
    --disposition callback \\
    --callback-at "2026-05-29T14:00:00Z" \\
    --notes "She said call back Thursday at 2pm"

  python tools/log_call.py \\
    --name "Mike Torres" --phone "+15554445555" --email "mike@example.com" \\
    --disposition meeting_booked \\
    --meeting-at "2026-05-30T15:00:00Z" \\
    --meeting-end "2026-05-30T15:30:00Z"

Dispositions: no_answer | voicemail | callback | meeting_booked | not_interested
"""
import argparse
import logging
import sys
import os

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

# Allow running from project root: python tools/log_call.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import ghl_client as ghl

load_dotenv()
logging.basicConfig(level=logging.WARNING)
console = Console()


def main():
    parser = argparse.ArgumentParser(
        description="Log a manual phone call outcome to GoHighLevel CRM"
    )
    parser.add_argument("--name", required=True, help="Prospect full name")
    parser.add_argument("--phone", required=True, help="Phone number e.g. +15551234567")
    parser.add_argument("--email", default=None, help="Prospect email (optional)")
    parser.add_argument(
        "--disposition",
        required=True,
        choices=["no_answer", "voicemail", "callback", "meeting_booked", "not_interested"],
        help="Call outcome",
    )
    parser.add_argument(
        "--callback-at",
        dest="callback_at",
        default=None,
        help="ISO 8601 datetime for callback task, e.g. 2026-05-29T14:00:00Z",
    )
    parser.add_argument(
        "--meeting-at",
        dest="meeting_at",
        default=None,
        help="ISO 8601 meeting start time",
    )
    parser.add_argument(
        "--meeting-end",
        dest="meeting_end",
        default=None,
        help="ISO 8601 meeting end time",
    )
    parser.add_argument("--notes", default=None, help="Optional notes to add to the contact")
    args = parser.parse_args()

    console.print(f"\n[bold]Logging call:[/bold] {args.name} — disposition: [cyan]{args.disposition}[/cyan]")

    # Step 1: upsert contact
    try:
        contact = ghl.upsert_contact(
            name=args.name,
            phone=args.phone,
            email=args.email,
        )
        contact_id = contact["id"]
        console.print(f"[green]✓[/green] Contact upserted — ID: {contact_id}")
    except ghl.GHLError as e:
        console.print(f"[red]✗ Failed to upsert contact:[/red] {e}")
        sys.exit(1)

    # Step 2: handle disposition
    try:
        result = ghl.handle_disposition(
            contact_id=contact_id,
            disposition=args.disposition,
            callback_dt=args.callback_at,
            meeting_dt=args.meeting_at,
            meeting_end_dt=args.meeting_end,
            contact_name=args.name,
            notes_text=args.notes,
        )
    except ghl.GHLError as e:
        console.print(f"[red]✗ Failed to handle disposition:[/red] {e}")
        sys.exit(1)

    # Print summary table
    table = Table(title="GHL Actions Completed", show_header=True, header_style="bold cyan")
    table.add_column("Action", style="bold")
    table.add_column("Result")

    table.add_row("Contact", f"{args.name} ({contact_id})")
    table.add_row("Disposition", args.disposition)

    for action, data in result.items():
        action_id = data.get("id", "ok") if isinstance(data, dict) else "ok"
        table.add_row(action.capitalize(), f"[green]✓[/green] {action_id}")

    if not result:
        table.add_row("Actions", "[yellow]No additional actions for this disposition[/yellow]")

    console.print(table)
    console.print(f"\n[bold green]Done.[/bold green] Check GHL contact: {args.name}\n")


if __name__ == "__main__":
    main()
