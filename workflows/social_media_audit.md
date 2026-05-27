# Workflow: Social Media Marketing Audit

## Objective
Produce a full social media marketing audit for a prospective client business.
Identify weaknesses, competitor advantages, and actionable ad opportunities for ABGrowthCo to pitch.

## Required Inputs
- `business_name`: Full business name as it appears on Google (e.g., `Joe's Pizza`)
- `location`: City and state (e.g., `Austin, TX`)

## Required Environment Variables
Check `.env` before starting. All tools will degrade gracefully to fallbacks if some are missing,
but the best results require all of these:

| Variable | Purpose | Where to get it |
|----------|---------|-----------------|
| `GOOGLE_PLACES_API_KEY` | Google reviews + competitor search | console.cloud.google.com → Enable Places API (New) |
| `META_ACCESS_TOKEN` | Meta ad data + social presence | developers.facebook.com/tools/explorer |
| `ANTHROPIC_API_KEY` | AI narrative in report | Already set |
| `GOOGLE_SHEET_ID` | Push summary row to sheet | From sheet URL: `/spreadsheets/d/{ID}/edit` |
| `SERPAPI_KEY` | Fallback for Google Places | Already set |
| `APIFY_API_TOKEN` | Fallback for Meta social data | apify.com → Settings → API tokens |

**Meta token permissions needed:** `ads_read`, `pages_read_engagement`, `instagram_basic`
**Meta token expiry:** 60 days. Click "Extend Access Token" in the Explorer to max out the expiry.
For never-expiring tokens: create a System User in Meta Business Manager.

## Output
- `.tmp/audit_{business_slug}_{location_slug}.md` — full Markdown report
- New row in the Google Sheet at `GOOGLE_SHEET_ID`

**Filename slug rule:** lowercase, apostrophes removed, spaces/commas → underscores
- `"Joe's Pizza"` + `"Austin, TX"` → `audit_joes_pizza_austin_tx.md`

---

## Steps

### Step 1: Fetch Google Reviews
```bash
python tools/fetch_google_reviews.py \
  --business "{business_name}" \
  --location "{location}" \
  --output .tmp/google_reviews.json
```

**Success:** `.tmp/google_reviews.json` created. Note the `primary_type` field — used for competitor search.

**Failure (exit code 1):**
- Check that `GOOGLE_PLACES_API_KEY` or `SERPAPI_KEY` is set in `.env`
- If business not found, try dropping suffixes (e.g., "LLC", "Inc") or alternate spellings
- If both APIs fail, you can manually create `.tmp/google_reviews.json` using this schema:
  ```json
  {
    "place_id": "", "name": "Joe's Pizza", "rating": 4.2, "review_count": 183,
    "primary_type": "restaurant", "all_types": ["restaurant"],
    "reviews": [], "data_source": "manual", "fetched_at": "2026-01-01T00:00:00+00:00"
  }
  ```
- **Do NOT proceed to Step 3 without this file** — it anchors competitor niche detection.

---

### Step 2: Check Meta Ad Library
```bash
python tools/fetch_meta_ads.py \
  --business "{business_name}" \
  --country US \
  --output .tmp/meta_ads.json
```

*(Can run in parallel with Step 1)*

**Success:** `.tmp/meta_ads.json` created. Note `active_ad_count` and `assessment`.

**Failure:**
- If `META_ACCESS_TOKEN` is missing: generate one at developers.facebook.com/tools/explorer
- If Playwright is not installed: `pip install playwright && playwright install chromium`
- If both fail: the tool exits 0 with `"assessment": "unknown"`. The audit can continue.

---

### Step 3: Find Competitors
```bash
python tools/find_competitors.py \
  --business "{business_name}" \
  --location "{location}" \
  --reviews-file .tmp/google_reviews.json \
  --output .tmp/competitors.json
```

*(Run after Step 1 completes)*

**Success:** `.tmp/competitors.json` with 3-5 competitor objects.

**Failure / fewer than 3 results:**
- Try overriding the niche manually:
  ```bash
  python tools/find_competitors.py \
    --business "{business_name}" \
    --location "{location}" \
    --niche "pizza restaurant" \
    --output .tmp/competitors.json
  ```
- If still failing, check `GOOGLE_PLACES_API_KEY` and `SERPAPI_KEY`

---

### Step 4: Analyze Social Presence
```bash
python tools/fetch_social_presence.py \
  --business "{business_name}" \
  --location "{location}" \
  --output .tmp/social_presence.json
```

*(Can run in parallel with Step 3)*

**Success:** `.tmp/social_presence.json` with Facebook and Instagram data.

**"Not found" result:** This is valid data — no social presence is itself an audit finding.
Do not retry more than once; proceed with the not-found result.

**If Meta Graph API fails:**
- Token may lack `pages_read_engagement` permission — regenerate with correct permissions
- Apify fallback activates automatically if `APIFY_API_TOKEN` is set

---

### Step 5: Compile Audit Report

First, determine the output filename:
- `{business_name}` → lowercase, remove apostrophes/special chars, spaces → underscores
- `{location}` → lowercase, remove commas, spaces → underscores
- Prefix with `audit_`

```bash
python tools/compile_audit_report.py \
  --business "{business_name}" \
  --location "{location}" \
  --reviews-file .tmp/google_reviews.json \
  --ads-file .tmp/meta_ads.json \
  --competitors-file .tmp/competitors.json \
  --social-file .tmp/social_presence.json \
  --output .tmp/audit_{slug}.md
```

**Success:** Markdown report created at `.tmp/audit_{slug}.md`
**Review the report before pushing to sheets** — spot check numbers and narrative quality.

**If `ANTHROPIC_API_KEY` is missing:**
The data tables will still be generated. Narrative sections will show:
`[Narrative generation skipped — add ANTHROPIC_API_KEY to .env]`

---

### Step 6: Push to Google Sheets
```bash
python tools/push_to_sheets.py \
  --business "{business_name}" \
  --location "{location}" \
  --reviews-file .tmp/google_reviews.json \
  --ads-file .tmp/meta_ads.json \
  --competitors-file .tmp/competitors.json \
  --social-file .tmp/social_presence.json \
  --report-file .tmp/audit_{slug}.md \
  --sheet-id "$GOOGLE_SHEET_ID"
```

**Success:** Confirmation printed with row number.

**If `credentials.json` is missing:**
1. Go to console.cloud.google.com → APIs & Services → Credentials
2. Create Service Account → Actions → Manage keys → Add key → JSON
3. Download and save as `credentials.json` in the project root
4. Share the Google Sheet with the service account email (find it in the JSON as `client_email`) — give Editor access
5. Re-run Step 6

**If Sheet push fails completely:** The tool prints the row as CSV to stderr.
You can paste it manually.

---

## Parallel Execution (faster)

Steps 1 and 2 can run simultaneously.
Steps 3 and 4 can run simultaneously after Step 1 completes.

```
Start simultaneously:
  Step 1: fetch_google_reviews   ─┐
  Step 2: fetch_meta_ads          ┘ (wait for both)

After Step 1 completes, start simultaneously:
  Step 3: find_competitors        ─┐
  Step 4: fetch_social_presence    ┘ (wait for both)

Step 5: compile_audit_report   (after all 4 data files exist)
Step 6: push_to_sheets          (after Step 5)
```

---

## Edge Cases

### Business has multiple locations / franchise
The fuzzy name matcher in `fetch_social_presence.py` picks the closest match.
If the wrong Facebook page is selected, it will show in the output.
Future fix: accept `--facebook-page-url` as an override argument.

### Business not on Google at all
Rare, but possible for very new or very small businesses.
This is itself a major audit finding — no Google presence is critical.
Create a manual `google_reviews.json` and note the absence explicitly in the report.

### Meta Access Token expired
Tokens last 60 days by default. When you get a 400 OAuthException:
1. Go to developers.facebook.com/tools/explorer
2. Regenerate the token with the same permissions
3. Update `META_ACCESS_TOKEN` in `.env`
4. Document the new expiry date here: **Last renewed:** *(update this when you renew)*

For a permanent solution: create a System User in Meta Business Manager.
System User tokens never expire.

### Rate limits
- Google Places: 1,000 requests/day on free tier
- Meta Ad Library: 200 calls/hour per token (`tenacity` handles retries automatically)
- Anthropic: Default tier — 50 requests/minute (not an issue for single audits)
- SerpAPI: 100 free searches/month

### Report narrative seems generic
The AI narrative quality depends on data richness. If several fields are `null` or `"unknown"`,
Claude has less to work with. Try to ensure Google reviews and competitor data are populated
before running `compile_audit_report.py`.

---

## What to Do After Running the Audit

1. Open `.tmp/audit_{slug}.md` and read through the report
2. Verify the competitor table looks accurate
3. Use the report as the basis for an outreach email or pitch deck
4. Archive it: copy to Google Drive or a client folder outside `.tmp/`
   (`.tmp/` files are disposable and may be overwritten on the next run)
