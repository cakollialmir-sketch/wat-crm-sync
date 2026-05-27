#!/usr/bin/env python3
"""
Compile all audit data into a full Markdown report with AI-generated narrative sections.

Reads the four JSON files from previous steps, calls Claude to generate narrative,
and assembles a structured report.

Usage:
  python tools/compile_audit_report.py \
    --business "Joe's Pizza" \
    --location "Austin, TX" \
    --reviews-file .tmp/google_reviews.json \
    --ads-file .tmp/meta_ads.json \
    --competitors-file .tmp/competitors.json \
    --social-file .tmp/social_presence.json \
    --output .tmp/audit_joes_pizza_austin_tx.md
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
PLACEHOLDER = "[Narrative generation skipped — add ANTHROPIC_API_KEY to .env]"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path: str) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Required file not found: {path}")
    with open(path) as f:
        return json.load(f)


def extract_xml(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return match.group(1).strip() if match else PLACEHOLDER


def stars(rating) -> str:
    if rating is None:
        return "N/A"
    filled = round(rating)
    return "★" * filled + "☆" * (5 - filled) + f" ({rating})"


def na(val, suffix="") -> str:
    if val is None:
        return "N/A"
    return f"{val}{suffix}"


# ---------------------------------------------------------------------------
# AI narrative generation
# ---------------------------------------------------------------------------

def generate_narrative(data_summary: dict) -> dict:
    if not ANTHROPIC_API_KEY:
        return {tag: PLACEHOLDER for tag in
                ["business_overview", "doing_wrong", "competitors_doing_better", "opportunities", "ad_strategy"]}

    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    system_prompt = (
        "You are a senior marketing strategist at ABGrowthCo, a digital marketing agency. "
        "You write audits that help you win new clients. Be direct, data-driven, and specific. "
        "Do not hedge or use corporate filler language. "
        "Each section should feel like honest advice from someone who has done this a hundred times."
    )

    user_prompt = f"""
You are reviewing a social media marketing audit for a prospective client.
Here is the collected data:

<audit_data>
{json.dumps(data_summary, indent=2)}
</audit_data>

Write the following sections of the audit report. Wrap each section in XML tags exactly as shown.
Be specific — reference actual numbers from the data. Avoid vague statements.

<business_overview>
2-3 sentence summary of the business's current digital marketing position based on the data.
Include their Google rating and review count, whether they're running Meta ads, and their social presence status.
</business_overview>

<doing_wrong>
Bullet list of 3-5 specific weaknesses identified from the data.
Each bullet should name the problem and briefly explain why it matters for revenue.
</doing_wrong>

<competitors_doing_better>
2-3 paragraphs. Compare the audited business to their top competitors.
What are competitors doing in terms of reviews, ads, and social media that this business is not?
Be specific about which competitors and what they're doing.
</competitors_doing_better>

<opportunities>
Bullet list of 3-4 specific opportunities ABGrowthCo can exploit to grow this client.
Each bullet should be actionable and tied to a gap identified in the data.
</opportunities>

<ad_strategy>
A concrete Meta/Instagram ad strategy recommendation.
Include: campaign objective (awareness vs. conversion), target audience (demographics + interests),
ad format recommendation (video, carousel, static), budget suggestion, and what the first
30 days of ads should accomplish. Be specific and opinionated.
</ad_strategy>
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": user_prompt}],
        system=system_prompt,
    )

    response_text = message.content[0].text
    return {
        "business_overview": extract_xml(response_text, "business_overview"),
        "doing_wrong": extract_xml(response_text, "doing_wrong"),
        "competitors_doing_better": extract_xml(response_text, "competitors_doing_better"),
        "opportunities": extract_xml(response_text, "opportunities"),
        "ad_strategy": extract_xml(response_text, "ad_strategy"),
    }


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def build_report(business: str, location: str, reviews: dict, ads: dict,
                 competitors: dict, social: dict, narrative: dict) -> str:
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    fb = social.get("facebook", {})
    ig = social.get("instagram", {})

    # Competitor table rows
    comp_rows = []
    for c in competitors.get("competitors", []):
        ad_info = c.get("meta_ad_assessment", "unknown")
        comp_rows.append(
            f"| {c.get('name', 'N/A')} | {na(c.get('rating'))}★ | "
            f"{na(c.get('review_count'))} | {ad_info} |"
        )
    comp_table = "\n".join(comp_rows) if comp_rows else "| No competitor data found | — | — | — |"

    # Social presence rows
    fb_status = fb.get("presence_quality", "unknown") if fb.get("found") else "Not found"
    ig_status = f"@{ig.get('username')} ({na(ig.get('followers_count'))} followers)" if ig.get("found") else "Not found"

    report = f"""# Social Media Marketing Audit: {business} — {location}
*Generated: {today} | Prepared by ABGrowthCo*

---

## 1. Business Overview

| Metric | Value |
|--------|-------|
| Google Rating | {stars(reviews.get("rating"))} |
| Google Reviews | {na(reviews.get("review_count"))} |
| Business Type | {reviews.get("primary_type", "N/A")} |
| Active Meta Ads | {na(ads.get("active_ad_count"))} ({ads.get("assessment", "unknown")}) |
| Facebook Presence | {fb_status} |
| FB Followers | {na(fb.get("follower_count"))} |
| Days Since Last FB Post | {na(fb.get("last_post_days_ago"), "d")} |
| FB Engagement Rate | {na(fb.get("engagement_rate_pct"), "%")} |
| Instagram | {ig_status} |
| Overall Social Score | {social.get("overall_social_score", "N/A")} / 5 |

{narrative["business_overview"]}

---

## 2. What They Are Doing Wrong

{narrative["doing_wrong"]}

---

## 3. What Competitors Are Doing Better

### Competitor Comparison

| Business | Rating | Reviews | Meta Ads |
|----------|--------|---------|----------|
| **{business} (audited)** | {stars(reviews.get("rating"))} | {na(reviews.get("review_count"))} | {ads.get("assessment", "unknown")} |
{comp_table}

{narrative["competitors_doing_better"]}

---

## 4. Opportunities ABGrowthCo Can Exploit

{narrative["opportunities"]}

---

## 5. Recommended Ad Strategy (Meta/Instagram)

{narrative["ad_strategy"]}

---

### Recent Customer Reviews (sample)

"""

    for rv in reviews.get("reviews", [])[:3]:
        text = rv.get("text", "").replace("\n", " ").strip()
        if text:
            report += f'> **{rv.get("rating")}★** — "{text[:300]}"\n>\n'

    report += f"""
---

*Data sources: {reviews.get("data_source", "N/A")}, {ads.get("data_source", "N/A")}, {social.get("data_source", "N/A")}*
*Audit generated by ABGrowthCo automated audit system*
"""

    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Compile social media audit report")
    parser.add_argument("--business", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--reviews-file", required=True)
    parser.add_argument("--ads-file", required=True)
    parser.add_argument("--competitors-file", required=True)
    parser.add_argument("--social-file", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output) if os.path.dirname(args.output) else ".", exist_ok=True)

    print("Loading data files...")
    reviews = load_json(args.reviews_file)
    ads = load_json(args.ads_file)
    competitors = load_json(args.competitors_file)
    social = load_json(args.social_file)

    # Summary dict for Claude
    data_summary = {
        "business": args.business,
        "location": args.location,
        "google": {
            "rating": reviews.get("rating"),
            "review_count": reviews.get("review_count"),
            "primary_type": reviews.get("primary_type"),
            "sample_reviews": reviews.get("reviews", [])[:5],
        },
        "meta_ads": {
            "active_count": ads.get("active_ad_count"),
            "assessment": ads.get("assessment"),
        },
        "competitors": competitors.get("competitors", []),
        "social": {
            "facebook": social.get("facebook", {}),
            "instagram": social.get("instagram", {}),
            "overall_score": social.get("overall_social_score"),
        },
    }

    if ANTHROPIC_API_KEY:
        print("Generating AI narrative sections via Claude...")
    else:
        print("Warning: ANTHROPIC_API_KEY not set. Narrative sections will be placeholder text.", file=sys.stderr)

    narrative = generate_narrative(data_summary)

    print("Assembling report...")
    report_md = build_report(
        args.business, args.location,
        reviews, ads, competitors, social, narrative
    )

    with open(args.output, "w") as f:
        f.write(report_md)

    print(f"Report saved to {args.output}")
    print(f"  Sections: Business Overview, Weaknesses, Competitors, Opportunities, Ad Strategy")


if __name__ == "__main__":
    main()
