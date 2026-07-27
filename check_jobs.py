#!/usr/bin/env python3
"""
Bank analyst job alert bot.

For each source in config.json (TheMuse aggregator searches, and each
firm's own career site), loads the page in a real headless browser,
pulls out every link whose visible text looks like a job posting
matching your keywords, compares against the last-seen snapshot, and
emails you only the NEW postings -- each with its title AND a direct
link to the job's page.

Run on a schedule (see .github/workflows/check_jobs.yml) so it works
even when your laptop is closed.
"""

import json
import os
import re
import smtplib
import time
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_DIR = Path(__file__).parent / "state"
STATE_DIR.mkdir(exist_ok=True)


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def fetch_postings(page, url, wait_ms=8000):
    """Load a page in a real (headless) browser, wait for JS to render the
    job listings, and return a list of {"text": ..., "href": ...} for every
    link on the page. Using a real browser instead of a plain HTTP fetch is
    what makes this work on career sites (Goldman, BlackRock, Blackstone,
    etc.) that render their job listings client-side with JavaScript.
    """
    page.goto(url, timeout=60000, wait_until="domcontentloaded")
    # Give client-side rendering time to populate the job list. Some career
    # sites (e.g. Citi) are slower than others, so this is generous.
    page.wait_for_timeout(wait_ms)

    # Pull every link's visible text and absolute href directly from the DOM.
    # This is what lets us attach a clickable URL to each matched posting,
    # instead of just having plain text with no link.
    raw_links = page.eval_on_selector_all(
        "a",
        """els => els.map(el => ({
            text: (el.innerText || "").trim(),
            href: el.getAttribute("href") || ""
        }))"""
    )

    postings = []
    seen = set()
    for link in raw_links:
        text = " ".join(link["text"].split())  # collapse internal whitespace
        href = link["href"]
        if not text or not href:
            continue
        if text in seen:
            continue
        seen.add(text)
        absolute_href = urljoin(url, href)
        postings.append({"text": text, "href": absolute_href})

    return postings


# Lines containing any of these are page chrome / summary text, not actual
# job postings -- drop them even if they happen to contain a keyword.
NOISE_PATTERNS = [
    r"\bresults? found\b",
    r"\bresults? for\b",
    r"^\d+\s+(results?|jobs?)\b",
    r"\bno (results?|jobs?|matches) found\b",
    r"\bsearch (results?|jobs?)\b",
    r"\bfilter\b",
    r"\bsort by\b",
    r"\bshowing \d+",
    r"\bpage \d+ of \d+\b",
    r"\bsubscribe\b",
    r"\bcreate (a |an )?(job )?alert\b",
    r"\bsign in\b",
    r"\bcookie\b",
]


def is_noise(text):
    low = text.lower()
    return any(re.search(pat, low) for pat in NOISE_PATTERNS)


def is_stale_cycle(text, stale_year_terms):
    """Drop postings that explicitly mention an earlier recruiting cycle
    (e.g. 'Class of 2026', 'Summer 2025') -- you only want summer 2027+."""
    low = text.lower()
    return any(term.lower() in low for term in stale_year_terms)


def matches_keywords(text, role_terms, domain_terms):
    """A posting counts as a match if its text contains an analyst-type
    role term AND a relevant domain term -- not necessarily as one exact
    phrase. This catches titles like 'Analyst, Equity Capital Markets' or
    'Investment Banking - M&A - Analyst' that a single fixed phrase like
    "investment banking analyst" would miss.
    """
    low = text.lower()
    has_role = any(rt.lower() in low for rt in role_terms)
    has_domain = any(dt.lower() in low for dt in domain_terms)
    return has_role and has_domain


def load_previous_state(source_key):
    """Returns a dict of {title_text: href} seen as of the last run."""
    path = STATE_DIR / f"{source_key}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(source_key, postings_dict):
    path = STATE_DIR / f"{source_key}.json"
    with open(path, "w") as f:
        json.dump(postings_dict, f, indent=2, sort_keys=True)


def send_email(subject, body, to_addr, from_addr, app_password):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(from_addr, app_password)
        server.sendmail(from_addr, [to_addr], msg.as_string())


def check_source(page, source_key, url, role_terms, domain_terms, stale_year_terms,
                  company_filter=None, label=None):
    """Fetch one URL, return a dict of {title_text: href} that is NEW
    since the last run."""
    label = label or source_key
    postings = fetch_postings(page, url)

    matching = {}
    for p in postings:
        text, href = p["text"], p["href"]
        if len(text) >= 200:  # drop paragraphs/descriptions, keep title-like text
            continue
        if not matches_keywords(text, role_terms, domain_terms):
            continue
        if is_noise(text):
            continue
        if is_stale_cycle(text, stale_year_terms):
            continue
        if company_filter and not any(c.lower() in text.lower() for c in company_filter):
            continue
        matching[text] = href

    # Diagnostic: shows up in the Actions log every run, so you can see
    # whether a source is finding zero links at all (page didn't load /
    # selector issue), finding links but zero matches (keyword/structure
    # mismatch -- e.g. title text isn't inside the <a> tag), or working
    # normally.
    print(f"[INFO] {label}: {len(postings)} links found on page, "
          f"{len(matching)} matched your keywords")
    if postings and not matching:
        sample = [p["text"][:80] for p in postings[:5] if p["text"]]
        print(f"[INFO] {label}: sample of link text seen (first 5): {sample}")

    previous = load_previous_state(source_key)
    new_postings = {text: href for text, href in matching.items() if text not in previous}

    save_state(source_key, matching)
    return new_postings


def main():
    config = load_config()
    role_terms = config["role_terms"]
    domain_terms = config["domain_terms"]
    stale_year_terms = config.get("stale_year_terms", [])
    target_companies = config.get("target_companies", [])
    all_new = {}  # {source_name: {title_text: href}}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        )
        page = context.new_page()

        # Source 1: TheMuse aggregator searches, filtered to target companies.
        for i, url in enumerate(config.get("aggregator_searches", [])):
            source_key = f"aggregator_{i}"
            try:
                new_postings = check_source(
                    page, source_key, url, role_terms, domain_terms, stale_year_terms,
                    company_filter=target_companies, label=f"TheMuse search #{i+1}",
                )
            except Exception as e:
                print(f"[WARN] aggregator search {url}: failed ({e})")
                continue
            if new_postings:
                all_new[f"TheMuse search #{i+1}"] = new_postings
            time.sleep(1)

        # Source 2: each firm's own career site directly.
        for firm in config.get("direct_firm_urls", []):
            name = firm["name"]
            url = firm["url"]
            firm_key = re.sub(r"[^a-z0-9]+", "_", name.lower())
            try:
                new_postings = check_source(
                    page, firm_key, url, role_terms, domain_terms, stale_year_terms,
                    label=name,
                )
            except Exception as e:
                print(f"[WARN] {name}: failed to fetch ({e})")
                continue
            if new_postings:
                all_new[name] = new_postings
            time.sleep(1)

        browser.close()

    if not all_new:
        print("No new postings found.")
        return

    # Build email body -- each posting shown as its title with a clickable link.
    body_parts = []
    for source_name, postings in all_new.items():
        body_parts.append(f"=== {source_name} ===")
        for title, href in sorted(postings.items()):
            body_parts.append(f"  - {title}")
            body_parts.append(f"    {href}")
        body_parts.append("")
    body = "\n".join(body_parts)

    print(body)

    # Email settings pulled from environment (set as GitHub Actions secrets)
    to_addr = os.environ.get("ALERT_TO_EMAIL")
    from_addr = os.environ.get("ALERT_FROM_EMAIL")
    app_password = os.environ.get("ALERT_EMAIL_APP_PASSWORD")

    if to_addr and from_addr and app_password:
        subject = f"New analyst postings: {', '.join(all_new.keys())}"
        send_email(subject, body, to_addr, from_addr, app_password)
        print("Email sent.")
    else:
        print("[INFO] Email env vars not set — printed results only, no email sent.")


if __name__ == "__main__":
    main()
