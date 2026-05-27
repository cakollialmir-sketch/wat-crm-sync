#!/usr/bin/env python3
"""
Print all GHL pipeline, stage, calendar, and workflow IDs for this location.
Run this any time you add new stages or want to verify current IDs.

Usage: python tools/fetch_ghl_ids.py
"""
import os
import sys
import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
console = Console()

TOKEN = os.getenv("GHL_API_TOKEN")
LOC   = os.getenv("GHL_LOCATION_ID")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Version": "2021-07-28"}

def get(path, params=None):
    r = requests.get(f"https://services.leadconnectorhq.com{path}", headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def main():
    if not TOKEN or not LOC:
        console.print("[red]GHL_API_TOKEN or GHL_LOCATION_ID not set in .env[/red]")
        sys.exit(1)

    # Pipelines + stages
    console.print("\n[bold cyan]── Pipelines & Stages ──[/bold cyan]")
    data = get("/opportunities/pipelines", {"locationId": LOC})
    for p in data.get("pipelines", []):
        t = Table(title=f"Pipeline: {p['name']}  |  id={p['id']}", show_header=True, header_style="bold")
        t.add_column("Stage Name")
        t.add_column("Stage ID")
        for s in p.get("stages", []):
            t.add_row(s["name"], s["id"])
        console.print(t)

    # Calendars
    console.print("\n[bold cyan]── Calendars ──[/bold cyan]")
    cal_data = get("/calendars/", {"locationId": LOC})
    t2 = Table(show_header=True, header_style="bold")
    t2.add_column("Calendar Name")
    t2.add_column("Calendar ID")
    for c in cal_data.get("calendars", []):
        t2.add_row(c.get("name", ""), c.get("id", ""))
    console.print(t2)

    # Workflows
    console.print("\n[bold cyan]── Workflows (Automations) ──[/bold cyan]")
    try:
        wf_data = get("/workflows/", {"locationId": LOC})
        t3 = Table(show_header=True, header_style="bold")
        t3.add_column("Workflow Name")
        t3.add_column("Workflow ID")
        t3.add_column("Status")
        for w in wf_data.get("workflows", []):
            t3.add_row(w.get("name", ""), w.get("id", ""), w.get("status", ""))
        console.print(t3)
    except Exception as e:
        console.print(f"[yellow]Could not fetch workflows: {e}[/yellow]")

if __name__ == "__main__":
    main()
