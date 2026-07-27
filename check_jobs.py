#!/usr/bin/env python3
"""
Bank analyst job alert bot.

For each firm in config.json, fetches the career-search page, extracts
lines that look like job postings matching your keywords, compares
against the last-seen snapshot, and emails you only the NEW postings.

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

from playwright.sync_api import sync_playwright

CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_DIR = Path(__file__).parent / "state"
STATE_DIR.mkdir(exist_ok=True)


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def fetch_text_lines(page, url, wait_ms=4000):
    """Load a page in a real (headless) browser, wait for JS to render the
    job listings, and return a list of visible text lines (deduped, stripped).
    Using a real browser instead of a plain HTTP fetch is what makes this
    work on career sites (Goldman, BlackRock, Blackstone, etc.) that render
    their job listings client-side with JavaScript.
    """
    page.goto(url, timeout=45000, wait_until="domcontentloaded")
    # Give client-side rendering time to populate the job list.
    page.wait_for_timeout(wait_ms)

    text = page.inner_text("body")
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]  # drop blanks
    # dedupe while preserving order
    seen = set()
    out = []
    for ln in lines:
        if ln not in seen:
            seen.add(ln)
            out.append(ln)
    return out


def matches_keywords(line, keywords):
    low = line.lower()
    return any(kw.lower() in low for kw in keywords)


def load_previous_state(firm_key):
    path = STATE_DIR / f"{firm_key}.json"
    if path.exists():
        with open(path) as f:
            return set(json.load(f))
    return set()


def save_state(firm_key, lines_set):
    path = STATE_DIR / f"{firm_key}.json"
    with open(path, "w") as f:
        json.dump(sorted(lines_set), f, indent=2)


def send_email(subject, body, to_addr, from_addr, app_password):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(from_addr, app_password)
        server.sendmail(from_addr, [to_addr], msg.as_string())


def check_source(page, source_key, url, keywords, company_filter=None):
    """Fetch one URL, return (matching_lines, new_lines_vs_last_run)."""
    lines = fetch_text_lines(page, url)
    matching = {ln for ln in lines if matches_keywords(ln, keywords)}

    if company_filter:
        matching = {
            ln for ln in matching
            if any(c.lower() in ln.lower() for c in company_filter)
        }

    previous = load_previous_state(source_key)
    new_lines = matching - previous
    save_state(source_key, matching)
    return new_lines


def main():
    config = load_config()
    keywords = config["keywords"]
    target_companies = config.get("target_companies", [])
    all_new = {}

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
                new_lines = check_source(
                    page, source_key, url, keywords, company_filter=target_companies
                )
            except Exception as e:
                print(f"[WARN] aggregator search {url}: failed ({e})")
                continue
            if new_lines:
                all_new[f"TheMuse search #{i+1}"] = sorted(new_lines)
            time.sleep(1)

        # Source 2: each firm's own career site directly.
        for firm in config.get("direct_firm_urls", []):
            name = firm["name"]
            url = firm["url"]
            firm_key = re.sub(r"[^a-z0-9]+", "_", name.lower())
            try:
                new_lines = check_source(page, firm_key, url, keywords)
            except Exception as e:
                print(f"[WARN] {name}: failed to fetch ({e})")
                continue
            if new_lines:
                all_new[name] = sorted(new_lines)
            time.sleep(1)

        browser.close()

    if not all_new:
        print("No new postings found.")
        return

    # Build email body
    body_parts = []
    for firm, lines in all_new.items():
        body_parts.append(f"=== {firm} ===")
        for ln in lines:
            body_parts.append(f"  - {ln}")
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
