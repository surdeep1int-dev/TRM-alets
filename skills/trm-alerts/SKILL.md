---
name: trm-alerts
description: Fetches 3-4 TRM Confluence pages (last ~1 month) and analyses every alert into 4 columns — Alert Name, Description, Reason, Action Taken. Writes ~/trm_alerts.html and auto-opens it.
---

# TRM Alerts Analyser

## Steps

### 1. Validate env vars

Check that `CONFLUENCE_EMAIL` and `CONFLUENCE_TOKEN` are set. If either is missing, stop and tell the user:
```
export CONFLUENCE_EMAIL="your-email@swiggy.in"
export CONFLUENCE_TOKEN="your-atlassian-api-token"
```

### 2. Run

```bash
python3 ~/.claude/commands/trm_alerts.py "$ARGUMENTS"
```

## Usage examples

```bash
# Single URL — auto-fetches last 4 weeks for that team
/trm-alerts https://swiggy.atlassian.net/wiki/spaces/TPM/pages/123456

# Multiple explicit URLs (2-4 pages)
/trm-alerts https://...page1 https://...page2 https://...page3

# Custom output path
/trm-alerts https://...page1 --output ~/Desktop/alerts_report.html

# Dump raw HTML to debug column names
/trm-alerts https://...page1 --dump-html
```

## What it produces

| File | Contents |
|---|---|
| `~/trm_alerts.html` | Table grouped by team+week. Each alert row has: Alert Name, Description, Reason, Action Taken. Auto-opens in browser. |

## Column extraction logic

The script looks for headings matching:
- `critical alert`, `warning alert`, `p0 alert`, `p1 alert`, `alerts`

Under each heading it reads the **first table** and maps columns flexibly:
- **Name** — column whose header matches `alert`, `name`, `title`
- **Description** — column matching `description`, `what`, `indicates`, `details`
- **Reason** — column matching `reason`, `root cause`, `cause`, `why`, `trigger`
- **Action Taken** — column matching `action`, `ai`, `taken`, `resolution`, `fix`

If the page has free-text alerts (no table), it falls back to bullet-point parsing.

## Error handling

| Error | Fix |
|---|---|
| 401 / 403 | Check `CONFLUENCE_EMAIL` and `CONFLUENCE_TOKEN` |
| "0 alerts found" | Run with `--dump-html` to inspect headings; update `ALERT_SECTION_PATTERNS` |
| Missing packages | `pip3 install requests beautifulsoup4 lxml` |
