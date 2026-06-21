#!/usr/bin/env python3
"""
TRM Confluence Alerts Analyser.

Fetches 3-4 TRM pages (or auto-searches last 4 weeks for one team) and
extracts every alert into 4 columns:
  - Alert Name
  - Alert Description  (what the alert indicates)
  - Reason             (why it triggered)
  - Action Taken       (AIs / resolution, if any)

Required env vars:
  CONFLUENCE_EMAIL   – Atlassian account email
  CONFLUENCE_TOKEN   – Atlassian personal API token

Usage:
  python trm_alerts.py <url> [url2 url3 url4]
                       [--output ~/trm_alerts.html]
                       [--last N]          # auto-fetch last N weeks (default 4)
                       [--dry-run]
                       [--dump-html]
"""

import os, sys, re, argparse, subprocess, html as html_lib
from datetime import datetime, timedelta
from pathlib import Path

# ── Load .env if present ──────────────────────────────────────────────────────
_env_candidates = [
    Path(__file__).parent / ".env",                                    # ~/.claude/commands/.env
    Path.home() / "Desktop/trm-alerts/.env",                          # canonical project .env
    Path.home() / "Desktop/claude_skill/.env",
    Path.home() / "Desktop/Code/im-claude-marketplace/.env",
]
_env_file = next((p for p in _env_candidates if p.exists()), None)
if _env_file and _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"\''))
# alias: CONFLUENCE_API_TOKEN → CONFLUENCE_TOKEN
if not os.environ.get("CONFLUENCE_TOKEN") and os.environ.get("CONFLUENCE_API_TOKEN"):
    os.environ["CONFLUENCE_TOKEN"] = os.environ["CONFLUENCE_API_TOKEN"]

import json
import requests
from bs4 import BeautifulSoup, NavigableString

# ── Heading patterns for alert sections ───────────────────────────────────────
ALERT_SECTION_PATTERNS = [
    r"critical\s+alert",
    r"warning\s+alert",
    r"p0\s+alert",
    r"p1\s+alert",
    r"\balerts?\b",
    r"alert\s+summary",
    r"on.?call\s+alert",
    r"infra\s+alert",
]

# ── Team detection (mirrors extract-trm-csv) ──────────────────────────────────
TEAM_PATTERNS = [
    (r"im.?cg\b",                    "CG"),
    (r"im.?search|search\s+trm",     "Search"),
    (r"checkout\s+trm|im.?checkout", "Checkout"),
    (r"im.?postorder|post.?order",   "PostOrder"),
    (r"im.?discovery",               "Discovery"),
    (r"ads\s*sos|ads\s*trm",         "Ads"),
    (r"growthx|ucp\s*trm",           "GrowthX"),
]

TEAM_COLORS = ["#1a73e8", "#0f9d58", "#e37400", "#a142f4", "#d93025", "#00796b"]

TITLE_QUERIES = {
    "CG":        "IM-CG TRM",
    "Search":    "IM-SEARCH TRM",
    "GrowthX":   "GrowthX",
    "Checkout":  "Checkout TRM",
    "PostOrder": "PostOrder TRM",
    "Discovery": "IM-Discovery TRM",
    "Ads":       "Ads TRM",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def env(var):
    v = os.environ.get(var, "")
    if not v:
        print(f"ERROR: ${var} is not set.\n  export {var}=<value>")
        sys.exit(1)
    return v


def extract_page_id(url):
    m = re.search(r"/pages/(\d+)", url)
    if not m:
        raise ValueError(f"Cannot extract page ID from: {url}")
    return m.group(1)


def extract_base_url(url):
    m = re.match(r"(https?://[^/]+)", url)
    return m.group(1) if m else "https://swiggy.atlassian.net"


def extract_space_key(url):
    m = re.search(r"/spaces/([^/]+)/", url)
    return m.group(1) if m else "TPM"


def detect_team(title):
    t = title.lower()
    for pattern, label in TEAM_PATTERNS:
        if re.search(pattern, t):
            return label
    m = re.search(r"im[-\s]+([a-z]+)", t)
    return m.group(1).capitalize() if m else "Unknown"


def extract_week_label(title):
    months = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*"
    m = re.search(
        rf"(\d{{1,2}})\s+{months}\s+(\d{{4}})\s*[-–]\s*(\d{{1,2}})\s+{months}\s+(\d{{4}})",
        title, re.I,
    )
    if m:
        return f"{m.group(1)}-{m.group(4)} {m.group(2).capitalize()} {m.group(3)}"
    m = re.search(rf"(\d{{1,2}})\s*[-–]\s*(\d{{1,2}})\s+{months}\s+(\d{{4}})", title, re.I)
    if m:
        return f"{m.group(1)}-{m.group(2)} {m.group(3).capitalize()} {m.group(4)}"
    m = re.search(rf"(\d{{1,2}})\s+{months}\s+(\d{{4}})", title, re.I)
    if m:
        return f"{m.group(1)} {m.group(2).capitalize()} {m.group(3)}"
    return datetime.now().strftime("%d %b %Y")


def tag_text(tag):
    if tag is None:
        return ""
    parts = []
    for child in tag.descendants:
        if isinstance(child, NavigableString):
            t = str(child).strip()
            if t:
                parts.append(t)
        elif hasattr(child, "name") and child.name == "br":
            parts.append(" | ")
    return " ".join(parts).strip()


def esc(text):
    return html_lib.escape(str(text)).replace("\n", "<br>")


# ── Confluence API ────────────────────────────────────────────────────────────

def fetch_page(base, page_id, email, token):
    url = f"{base}/wiki/rest/api/content/{page_id}?expand=body.storage,history,version"
    r = requests.get(url, auth=(email, token), timeout=30)
    if r.status_code == 401:
        print("ERROR: Confluence auth failed. Check CONFLUENCE_EMAIL / CONFLUENCE_TOKEN.")
        sys.exit(1)
    if r.status_code == 403:
        print("ERROR: No access to this Confluence page.")
        sys.exit(1)
    r.raise_for_status()
    data = r.json()
    title   = data.get("title", "")
    body    = data["body"]["storage"]["value"]
    creator = data.get("history", {}).get("createdBy", {}).get("displayName", "Unknown")
    return title, body, creator


def search_pages(base, space_key, title_query, email, token, limit=4):
    cql = f'space="{space_key}" AND title~"{title_query}" ORDER BY created DESC'
    url = (
        f"{base}/wiki/rest/api/content/search"
        f"?cql={requests.utils.quote(cql)}&limit={limit}&expand=history,version"
    )
    r = requests.get(url, auth=(email, token), timeout=30)
    r.raise_for_status()
    results = r.json().get("results", [])
    pages = []
    for p in results:
        pages.append({
            "id":      p["id"],
            "title":   p["title"],
            "creator": p.get("history", {}).get("createdBy", {}).get("displayName", "Unknown"),
            "url":     f"{base}/wiki/spaces/{space_key}/pages/{p['id']}",
        })
    return pages


# ── Alert extraction ──────────────────────────────────────────────────────────

# Header → canonical column key  (combined must come before description)
_COL_PATTERNS = {
    "combined":    [r"description\s*[&and]+\s*ai", r"description\s*[&and]+\s*action"],
    "name":        [r"alert\s*name", r"\bname\b", r"\btitle\b", r"\balert\b"],
    "description": [r"description", r"what.*indicates?", r"\bdetails?\b", r"\bindicates?\b", r"\bmeaning\b"],
    "reason":      [r"reason", r"root\s*cause", r"\bcause\b", r"\bwhy\b", r"trigger", r"fired\s*because"],
    "action":      [r"action\s*taken", r"\baction\b", r"\bai\b", r"resolution", r"\bfix\b", r"taken", r"steps?\s*taken"],
}

# Patterns that mark the start of "Actions taken" within a combined cell
_ACTION_SPLIT_RE = re.compile(
    r"(actions?\s+taken\s*:?|ai\s*:)", re.I
)


def _match_col(header_text):
    h = header_text.lower().strip()
    for key, pats in _COL_PATTERNS.items():
        for p in pats:
            if re.search(p, h):
                return key
    return None


def _split_combined(text):
    """Split 'Description & AIs' cell into (description, action)."""
    m = _ACTION_SPLIT_RE.search(text)
    if m:
        return text[:m.start()].strip(), text[m.start():].strip()
    return text.strip(), ""


def _parse_alert_table(table):
    """Return list of alert dicts from a <table> element."""
    rows = table.find_all("tr")
    if not rows:
        return []

    # Map column index → canonical key
    header_row = rows[0]
    headers = [tag_text(th) for th in header_row.find_all(["th", "td"])]
    col_map = {}  # index → key
    for i, h in enumerate(headers):
        key = _match_col(h)
        if key:
            col_map[i] = key

    # If no explicit "alert" name column, map second column as name (usually "Alert")
    if not any(v == "name" for v in col_map.values()):
        for i, h in enumerate(headers):
            if re.search(r"alert", h, re.I):
                col_map[i] = "name"
                break

    alerts = []
    for tr in rows[1:]:
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        alert = {"name": "", "description": "", "reason": "", "action": ""}
        for i, td in enumerate(cells):
            key = col_map.get(i)
            if not key:
                continue
            if key == "combined":
                alert["description"], alert["action"] = _split_combined(tag_text(td))
            else:
                alert[key] = tag_text(td).strip()
        # Keep row if it has a name or any content
        if any(alert.values()):
            if not alert["name"] and len(cells) > 1:
                alert["name"] = tag_text(cells[1]).strip()  # second col usually = alert name
            alerts.append(alert)

    return alerts


def _parse_alert_bullets(section_tag):
    """Fallback: parse bullet list as alert names with no detail."""
    alerts = []
    for li in section_tag.find_all("li"):
        text = tag_text(li).strip()
        if text:
            alerts.append({"name": text, "description": "", "reason": "", "action": ""})
    return alerts


def extract_alerts(soup):
    """
    Find every alert section heading, then extract alert rows from the
    table immediately following it (or bullet list as fallback).
    Returns list of dicts: {section, name, description, reason, action}
    """
    all_alerts = []
    seen_names = set()

    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        htext = tag_text(heading).strip()
        if not any(re.search(p, htext, re.I) for p in ALERT_SECTION_PATTERNS):
            continue

        section_label = htext
        alerts_in_section = []

        for sib in heading.find_next_siblings():
            if sib.name in ["h1", "h2", "h3", "h4"]:
                break
            if sib.name == "table":
                parsed = _parse_alert_table(sib)
                if parsed:
                    alerts_in_section.extend(parsed)
                    break  # one table per section is enough
            elif sib.name in ["ul", "ol"] and not alerts_in_section:
                alerts_in_section.extend(_parse_alert_bullets(sib))

        for a in alerts_in_section:
            key = (section_label, a["name"])
            if key in seen_names or not a["name"]:
                continue
            seen_names.add(key)
            all_alerts.append({**a, "section": section_label})

    return all_alerts


# ── Claude enrichment ────────────────────────────────────────────────────────

def enrich_with_claude(alerts):
    """
    For each alert:
    1. Generate a 1-2 line description of what the alert *means* (from alert name).
    2. If AI taken is empty, infer why no action was taken from the TRM context.
    Moves the raw TRM narrative from 'description' → 'reason', replacing
    'description' with the Claude-generated technical meaning.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or not alerts:
        return alerts

    try:
        import anthropic
    except ImportError:
        print("  [warn] anthropic package not installed, skipping AI enrichment")
        return alerts

    client = anthropic.Anthropic(api_key=api_key)

    payload = [
        {
            "idx": i,
            "alert_name": a["name"],
            "trm_context": a["description"],   # raw TRM narrative (becomes reason)
            "ai_taken": a["action"],
        }
        for i, a in enumerate(alerts)
    ]

    prompt = f"""You are an expert in Swiggy Instamart's engineering systems, monitoring, and on-call practices.

You will be given a list of alerts from TRM (Technical Risk Management) reports. For each alert:

1. **description**: Write 1-2 clear sentences explaining what this alert *means* — which service/component it monitors, what metric/condition triggers it, and what it indicates about the system health. Base this on the alert name patterns (e.g. HIGH_LATENCY, HYSTRIX_ERROR, DROP, CRITICAL, etc.) and common Instamart service names (im-checkout-service, dash-data-source, IMDS, IMCS, im-cg, etc.).

2. **reason_why_no_ai**: ONLY if "ai_taken" is empty — write 1 short sentence explaining why no action was likely taken, based on the "trm_context". Common reasons:
   - Singapore region alert (low traffic / TP, expected false alarm)
   - Alert auto-resolved or stabilised on its own
   - Issue was part of a broader P0 / tech warroom incident
   - Alert was investigated and deemed expected behaviour
   - No context available to determine reason
   If "ai_taken" is NOT empty, set this field to null.

Alerts to process:
{json.dumps(payload, indent=2)}

Respond with ONLY a valid JSON array. Each element: {{"idx": <int>, "description": "<string>", "reason_why_no_ai": "<string or null>"}}"""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        # Strip markdown code fences if present
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        enriched = json.loads(raw)
    except Exception as e:
        print(f"  [warn] Claude enrichment failed: {e}")
        return alerts

    lookup = {e["idx"]: e for e in enriched}
    for i, a in enumerate(alerts):
        info = lookup.get(i, {})
        # Move raw TRM narrative to reason (if reason not already set)
        if not a.get("reason") and a.get("description"):
            a["reason"] = a["description"]
        # Replace description with Claude-generated meaning
        if info.get("description"):
            a["description"] = info["description"]
        # Fill empty AI taken with inferred reason
        if not a.get("action") and info.get("reason_why_no_ai"):
            a["action"] = f"No AI taken — {info['reason_why_no_ai']}"

    return alerts


# ── Per-page processing ───────────────────────────────────────────────────────

def process_page(base_url, page_id, page_url, email, token, dump_html=False):
    title, body, creator = fetch_page(base_url, page_id, email, token)
    print(f"  Title:   {title}")

    if dump_html:
        print("\n--- RAW HTML (first 8000 chars) ---")
        print(body[:8000])
        return None

    week = extract_week_label(title)
    team = detect_team(title)
    print(f"  Week:    {week}  |  Team: {team}")

    soup   = BeautifulSoup(body, "html.parser")
    alerts = extract_alerts(soup)
    print(f"  Alerts found: {len(alerts)}, enriching with Claude...")
    alerts = enrich_with_claude(alerts)

    return {
        "week":         week,
        "team":         team,
        "source_url":   page_url,
        "extracted_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "alerts":       alerts,
    }


# ── HTML report generation ────────────────────────────────────────────────────

_COLS = [
    ("Alert Name",        "name"),
    ("Description",       "description"),
    ("Reason Triggered",  "reason"),
    ("AI Taken (if any)", "action"),
]

_CSS = """
* { box-sizing: border-box; }
body {
  font-family: Calibri, Arial, sans-serif;
  font-size: 12px;
  margin: 20px;
  background: #f5f5f5;
  color: #222;
}
h2 { color: #d04a02; margin-bottom: 2px; font-size: 16px; }
.updated { color: #999; font-size: 11px; margin-bottom: 14px; }
.week-block { margin-bottom: 28px; }
.week-header {
  background: #d04a02;
  color: #fff;
  padding: 6px 12px;
  font-size: 13px;
  font-weight: bold;
  border-radius: 4px 4px 0 0;
  display: flex;
  align-items: center;
  gap: 10px;
}
.week-header a { color: #ffe0c8; font-size: 11px; font-weight: normal; }
.team-section { margin-bottom: 16px; }
.team-label {
  display: inline-block;
  color: #fff;
  font-size: 11px;
  font-weight: bold;
  padding: 2px 8px;
  border-radius: 3px;
  margin: 8px 0 4px 0;
  letter-spacing: 0.3px;
}
table {
  border-collapse: collapse;
  width: 100%;
  table-layout: fixed;
  margin-bottom: 6px;
}
th, td {
  border: 1px solid #ddd;
  padding: 6px 10px;
  vertical-align: top;
  word-break: break-word;
}
thead tr th {
  position: sticky;
  top: 0;
  z-index: 50;
  background: #f0f0f0;
  color: #333;
  font-weight: bold;
  text-align: left;
  white-space: nowrap;
  box-shadow: 0 1px 3px rgba(0,0,0,0.12);
}
td { background: #fff; }
th:first-child, td:first-child { width: 18%; }
th:nth-child(2), td:nth-child(2) { width: 27%; }
th:nth-child(3), td:nth-child(3) { width: 27%; }
th:nth-child(4), td:nth-child(4) { width: 28%; }
.empty { color: #aaa; font-style: italic; }
a { color: #d04a02; }
"""


def generate_html(page_results, html_path):
    if not page_results:
        print("No data to write.")
        return

    # Group by week (preserve insertion order, newest first is assumed)
    from collections import OrderedDict
    weeks = OrderedDict()
    for pr in page_results:
        weeks.setdefault(pr["week"], []).append(pr)

    all_teams = list(dict.fromkeys(pr["team"] for pr in page_results))
    team_color = {t: TEAM_COLORS[i % len(TEAM_COLORS)] for i, t in enumerate(all_teams)}

    header_html = "".join(f"<th>{label}</th>" for label, _ in _COLS)
    body_html = ""

    for week, pages in weeks.items():
        # Build week header with links
        links_html = " &nbsp;|&nbsp; ".join(
            f'<a href="{html_lib.escape(p["source_url"])}" target="_blank">'
            f'{html_lib.escape(p["team"])}</a>'
            for p in pages if p.get("source_url")
        )
        body_html += (
            f'<div class="week-block">'
            f'<div class="week-header">'
            f'{html_lib.escape(week)}'
            f'{"  <span>" + links_html + "</span>" if links_html else ""}'
            f'</div>'
        )

        for page in pages:
            team  = page["team"]
            color = team_color.get(team, "#555")
            alerts = page.get("alerts", [])

            body_html += (
                f'<div class="team-section">'
                f'<span class="team-label" style="background:{color}">{html_lib.escape(team)}</span>'
            )

            if not alerts:
                body_html += '<p class="empty">No alerts found on this page.</p>'
            else:
                body_html += f"<table><thead><tr>{header_html}</tr></thead><tbody>"
                for a in alerts:
                    row_cells = ""
                    for _, key in _COLS:
                        val = a.get(key, "").strip()
                        cell_class = ' class="empty"' if not val else ""
                        display    = esc(val) if val else "—"
                        row_cells += f"<td{cell_class}>{display}</td>"
                    body_html += f"<tr>{row_cells}</tr>"
                body_html += "</tbody></table>"

            body_html += "</div>"  # .team-section

        body_html += "</div>"  # .week-block

    updated = datetime.now().strftime("%d %b %Y %H:%M")
    total_alerts = sum(len(p.get("alerts", [])) for p in page_results)

    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TRM Alerts Analysis</title>
<style>{_CSS}</style>
</head>
<body>
<h2>TRM Alerts — Last 1 Month</h2>
<p class="updated">
  Last updated: {updated} &nbsp;|&nbsp;
  {len(page_results)} page(s) analysed &nbsp;|&nbsp;
  {total_alerts} alert(s) extracted
</p>
{body_html}
</body>
</html>"""

    html_path.write_text(content, encoding="utf-8")
    print(f"  HTML written to: {html_path}")


def open_file(path):
    try:
        subprocess.Popen(["open", str(path)])
    except Exception:
        pass


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Analyse last 1 month of TRM alerts from Confluence.")
    ap.add_argument("urls", nargs="+", help="1-4 TRM Confluence page URLs")
    ap.add_argument("--last", type=int, default=4, metavar="N",
                    help="When 1 URL given: auto-fetch last N weeks (default: 4)")
    ap.add_argument("--output", default=os.path.expanduser("~/trm_alerts.html"))
    ap.add_argument("--dry-run",   action="store_true")
    ap.add_argument("--dump-html", action="store_true")
    args = ap.parse_args()

    email = env("CONFLUENCE_EMAIL")
    token = env("CONFLUENCE_TOKEN")

    urls     = args.urls
    base_url = extract_base_url(urls[0])

    # ── Auto-search if single URL provided ───────────────────────────────────
    if len(urls) == 1 and args.last > 1 and not args.dump_html:
        seed_id    = extract_page_id(urls[0])
        seed_title, _, _ = fetch_page(base_url, seed_id, email, token)
        team_name  = detect_team(seed_title)
        space_key  = extract_space_key(urls[0])
        query      = TITLE_QUERIES.get(team_name, seed_title.split("(")[0].strip())

        print(f"\nSearching last {args.last} pages for '{query}' in space '{space_key}'...")
        pages = search_pages(base_url, space_key, query, email, token, limit=args.last)
        if not pages:
            print("No pages found via search. Falling back to provided URL.")
            pages = [{"id": seed_id, "title": seed_title, "creator": "", "url": urls[0]}]
        else:
            print(f"  Found {len(pages)} page(s):")
            for p in pages:
                print(f"    • {p['title']}")
        page_list = [{"id": p["id"], "url": p["url"]} for p in pages]
    else:
        page_list = [{"id": extract_page_id(u), "url": u} for u in urls]

    # ── Process each page ─────────────────────────────────────────────────────
    results = []
    for p in page_list:
        print(f"\nFetching page {p['id']}...")
        result = process_page(base_url, p["id"], p["url"], email, token, dump_html=args.dump_html)
        if result:
            results.append(result)

    if args.dump_html or not results:
        return

    if args.dry_run:
        print("\n--- Dry Run ---")
        for r in results:
            print(f"  [{r['team']}] {r['week']} — {len(r['alerts'])} alert(s)")
            for a in r["alerts"][:3]:
                print(f"    • {a['name']}")
        print("\n[DRY RUN] No files written.")
        return

    html_path = Path(args.output)
    generate_html(results, html_path)

    total = sum(len(r.get("alerts", [])) for r in results)
    print(f"\nDone. {len(results)} page(s), {total} alert(s) extracted. Opening: {html_path}")
    open_file(html_path)


if __name__ == "__main__":
    main()
