# TRM Alerts Analyser

A Claude skill that fetches the last 4 weeks of TRM (Technical Risk Management) Confluence pages for an Instamart team and analyses every alert into a clean 4-column HTML report.

## What it does

- Fetches **3-4 TRM Confluence pages** (auto-searches last 4 weeks by team)
- Extracts every alert and enriches it via **Claude AI**
- Outputs `~/trm_alerts.html` — auto-opens in browser

### Output columns

| Column | Description |
|---|---|
| **Alert Name** | Name of the alert as it appears in the TRM |
| **Description** | 1-2 line explanation of what the alert *means* (Claude-generated from alert name) |
| **Reason Triggered** | What actually caused it to fire this week (from TRM report) |
| **AI Taken (if any)** | Actions taken by on-call, or inferred reason why no action was taken |

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/surdeep1int-dev/TRM-alets.git
cd TRM-alets
```

### 2. Install dependencies

```bash
pip3 install requests beautifulsoup4 lxml anthropic
```

### 3. Configure secrets

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```
CONFLUENCE_EMAIL=your-email@swiggy.in
CONFLUENCE_TOKEN=your-atlassian-api-token
ANTHROPIC_API_KEY=your-anthropic-api-key
```

- **Confluence token** — generate at https://id.atlassian.com/manage-profile/security/api-tokens
- **Anthropic API key** — get from https://console.anthropic.com

## Usage

```bash
# Single URL — auto-fetches last 4 weeks for that team
python3 skills/trm-alerts/scripts/trm_alerts.py \
  "https://swiggy.atlassian.net/wiki/spaces/TPM/pages/<page-id>"

# Pass 2-4 explicit page URLs
python3 skills/trm-alerts/scripts/trm_alerts.py \
  "https://.../page1" "https://.../page2" "https://.../page3"

# Control how many weeks to fetch
python3 skills/trm-alerts/scripts/trm_alerts.py "<url>" --last 4

# Custom output path
python3 skills/trm-alerts/scripts/trm_alerts.py "<url>" --output ~/Desktop/alerts.html

# Debug: dump raw Confluence HTML to inspect headings
python3 skills/trm-alerts/scripts/trm_alerts.py "<url>" --dump-html
```

## As a Claude skill

If you have the `claude_skill` marketplace configured, invoke via:

```
/trm-alerts https://swiggy.atlassian.net/wiki/spaces/TPM/pages/<page-id>
```

## Supported teams

Auto-detects team from page title:

| Team | Title pattern |
|---|---|
| CG | `IM-CG TRM` |
| Search | `IM-SEARCH TRM` |
| Checkout | `Checkout TRM` |
| PostOrder | `PostOrder TRM` |
| Discovery | `IM-Discovery TRM` |
| GrowthX | `GrowthX` |
| Ads | `Ads TRM` |

## Troubleshooting

| Error | Fix |
|---|---|
| `401 / 403` | Check `CONFLUENCE_EMAIL` and `CONFLUENCE_TOKEN` in `.env` |
| `0 alerts found` | Run with `--dump-html` to inspect page headings |
| Claude enrichment skipped | Set `ANTHROPIC_API_KEY` in `.env` |
| Missing packages | `pip3 install requests beautifulsoup4 lxml anthropic` |
