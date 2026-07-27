# Bank Analyst Job Alert Bot

Checks career pages for Goldman Sachs, Morgan Stanley, Citi, Bank of
America, Barclays, Evercore, PIMCO, BlackRock, and Blackstone for new
Investment Banking / Capital Markets / Equity Research analyst
postings, and emails you the moment something new appears. Runs on a
free schedule via GitHub Actions, so it works even when your laptop
is closed.

## How this is built, and why

Two independent sources are checked every run, since neither is fully
reliable on its own:

1. **TheMuse.com aggregator searches** (`aggregator_searches` in
   `config.json`) — confirmed reliable: it's plain, server-rendered
   HTML with real job text, not JavaScript-loaded. These searches
   return jobs from many companies; the script keeps only lines that
   also mention one of your `target_companies`. This is the more
   dependable layer.
2. **Each firm's own career site directly** (`direct_firm_urls`) —
   more brittle. Several of these firms (confirmed for Goldman Sachs
   by directly testing it) render their job listings with JavaScript
   after the page loads, so a plain page-fetch sees nothing useful.
   The script uses a real headless browser (Playwright) instead of a
   simple HTTP request specifically to handle this — it actually
   loads the page and waits for the listings to render before reading
   the text. Even so, these URLs will go stale as firms redesign
   their career sites, faster than the aggregator will.

## Setup (~20 minutes total)

### 1. Sanity-check the direct firm URLs in `config.json`
The URLs under `direct_firm_urls` are best-effort — go to each firm's
careers page yourself, search "Analyst" + New York, and swap in the
exact resulting URL if it looks different from what's there. Treat
the aggregator searches as your reliable baseline and these as a
bonus layer that needs occasional (every couple months) re-checking.

### 2. Create a free GitHub repo
1. Create a new **private** GitHub repo.
2. Upload this whole folder into it (or `git init` + push from here).

### 3. Set up email alerts (Gmail example)
1. Use a Gmail account (a new free one just for this is fine).
2. Turn on 2-factor authentication on that account.
3. Create an **App Password**: Google Account → Security → 2-Step
   Verification → App Passwords → generate one for "Mail".
4. In your GitHub repo: Settings → Secrets and variables → Actions →
   New repository secret. Add three secrets:
   - `ALERT_TO_EMAIL` — the email you want alerts sent TO (can be the
     same address, or your personal email)
   - `ALERT_FROM_EMAIL` — the Gmail address sending the alert
   - `ALERT_EMAIL_APP_PASSWORD` — the app password from step 3

### 4. Turn it on
The workflow in `.github/workflows/check_jobs.yml` runs every 2 hours
automatically once it's in your repo. You can also trigger it
manually anytime from the repo's "Actions" tab → "Check analyst job
postings" → "Run workflow", which is a good way to test it works
before waiting for the schedule.

## How it works
- Each run opens a real headless browser (Playwright/Chromium) and
  loads every URL in both `aggregator_searches` and
  `direct_firm_urls`, waiting a few seconds for any JavaScript to
  finish rendering job listings.
- It extracts every line of visible text and keeps only lines that
  contain one of your keywords (in `config.json` — edit this list
  anytime, e.g. add "sales and trading analyst" or "credit research").
  For the aggregator searches, it additionally keeps only lines that
  mention one of your `target_companies`.
- It compares that against the snapshot saved from the last run
  (in `state/`). Anything new gets emailed to you; anything already
  seen is skipped.
- The snapshot is committed back to the repo automatically so the
  next run knows what's already been seen.

## Adjusting things later
- **Check more/less often**: edit the `cron` line in the workflow
  file. `"0 */2 * * *"` = every 2 hours. `"0 * * * *"` = every hour.
- **Add/remove firms**: edit the `firms` list in `config.json`.
- **Add/remove keywords**: edit the `keywords` list in `config.json`.
- **Add LinkedIn/Handshake coverage**: keep using their native saved
  job alerts (LinkedIn and Handshake actively block automated
  scraping, so this bot intentionally focuses on firms' own career
  pages, which is both more reliable and fully within terms of use).

## Limitations to know about
- Some career sites render job listings via JavaScript after the
  page loads, which a simple page-fetch won't see. If a firm never
  seems to produce hits even after fixing the URL, that's likely why
  — flag it and a slightly heavier scraping approach (headless
  browser) can be swapped in for just that firm.
- This complements, not replaces, LinkedIn/Handshake alerts — some
  postings may only appear on one channel and not the other.
