#!/usr/bin/env python3
"""
Append a summary row to the ABGrowthCo audit Google Sheet.

Requires:
  - credentials.json in project root (Google Service Account key)
  - The sheet must be shared with the service account email (Editor access)

Usage:
  python tools/push_to_sheets.py \
    --business "Joe's Pizza" \
    --location "Austin, TX" \
    --reviews-file .tmp/google_reviews.json \
    --ads-file .tmp/meta_ads.json \
    --competitors-file .tmp/competitors.json \
    --social-file .tmp/social_presence.json \
    --report-file .tmp/audit_joes_pizza_austin_tx.md \
    --sheet-id "$GOOGLE_SHEET_ID"
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

SHEET_HEADERS = [
    "Date",
    "Business",
    "Location",
    "Google Rating",
    "Review Count",
    "Active Meta Ads",
    "Meta Ad Assessment",
    "FB Followers",
    "FB Presence Quality",
    "Days Since Last FB Post",
    "IG Followers",
    "Social Score (1-5)",
    "Report File",
]


def load_json(path: str) -> dict:
    if not os.path.exists(path):
        print(f"Warning: {path} not found, using empty dict", file=sys.stderr)
        return {}
    with open(path) as f:
        return json.load(f)


def get_gspread_client():
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
            "  3. Share your Google Sheet with the service account email (Editor access)"
        )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    return gspread.authorize(creds)


def main():
    parser = argparse.ArgumentParser(description="Push audit summary to Google Sheets")
    parser.add_argument("--business", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--reviews-file", required=True)
    parser.add_argument("--ads-file", required=True)
    parser.add_argument("--competitors-file", required=True)
    parser.add_argument("--social-file", required=True)
    parser.add_argument("--report-file", required=True)
    parser.add_argument("--sheet-id", default=os.getenv("GOOGLE_SHEET_ID"))
    args = parser.parse_args()

    if not args.sheet_id:
        print("Error: --sheet-id not provided and GOOGLE_SHEET_ID not set in .env", file=sys.stderr)
        sys.exit(1)

    reviews = load_json(args.reviews_file)
    ads = load_json(args.ads_file)
    social = load_json(args.social_file)

    fb = social.get("facebook", {})
    ig = social.get("instagram", {})

    row = [
        datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        args.business,
        args.location,
        reviews.get("rating", ""),
        reviews.get("review_count", ""),
        ads.get("active_ad_count", ""),
        ads.get("assessment", ""),
        fb.get("follower_count", "") if fb.get("found") else "not found",
        fb.get("presence_quality", "") if fb.get("found") else "not found",
        fb.get("last_post_days_ago", "") if fb.get("found") else "",
        ig.get("followers_count", "") if ig.get("found") else "not found",
        social.get("overall_social_score", ""),
        os.path.abspath(args.report_file) if os.path.exists(args.report_file) else args.report_file,
    ]

    print(f"Connecting to Google Sheets (ID: {args.sheet_id})...")
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(args.sheet_id)
        worksheet = sheet.get_worksheet(0)

        # Write headers if sheet is empty
        existing = worksheet.get_all_values()
        if not existing or existing[0] != SHEET_HEADERS:
            if not existing:
                worksheet.append_row(SHEET_HEADERS, value_input_option="RAW")
                print("  Wrote header row.")
            # If headers exist but differ, warn but don't overwrite
            elif existing[0] != SHEET_HEADERS:
                print("Warning: existing header row differs from expected. Appending data anyway.", file=sys.stderr)

        worksheet.append_row(row, value_input_option="USER_ENTERED")
        row_count = len(worksheet.get_all_values())
        print(f"Row appended successfully at row {row_count}.")
        print(f"  Business: {args.business} — {args.location}")
        print(f"  Rating: {reviews.get('rating')} | Reviews: {reviews.get('review_count')}")
        print(f"  Meta ads: {ads.get('assessment')} | Social score: {social.get('overall_social_score')}/5")

    except Exception as e:
        print(f"Error pushing to Google Sheets: {e}", file=sys.stderr)
        print("\nFallback: here is the row data as CSV:", file=sys.stderr)
        print(",".join([str(v) for v in row]), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
