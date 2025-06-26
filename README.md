# Good Apps Core Web Vitals Slack Notification System

## Overview

This system automatically retrieves Core Web Vitals (LCP, INP, CLS) for `good-apps.jp` from the Chrome UX Report API daily and sends a simple notification to Slack. It is executed daily via GitHub Actions and posts updates using Slack Incoming Webhook.

## Features

* Retrieves Good/NI/Poor percentages for the origin from the CrUX API.
* Evaluates metrics based on Google's thresholds.
* Sends simple text notifications via Slack Webhook.
* Automatically runs daily at a scheduled time using GitHub Actions.

## Prerequisites

* Python 3.8 or later.
* The following secrets must be registered in GitHub:

  * `CRUX_API_KEY` (Chrome UX Report API key)
  * `SLACK_WEBHOOK_URL` (Slack Incoming Webhook URL)

## Directory Structure Example

```
├─ .github/workflows/
│   └─ crux_report.yml
├─ scripts/
│   └─ cwv_report.py
└─ README.md
```

## Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/your-org/good-apps-cwv-slack.git
   cd good-apps-cwv-slack
   ```

2. Create and activate a Python virtual environment:

   ```bash
   python -m venv venv
   source venv/bin/activate  # macOS/Linux
   venv\Scripts\activate     # Windows
   ```

3. Install dependencies:

   ```bash
   pip install requests
   ```

## Environment Variables

In GitHub Actions, repository secrets are used.

```
CRUX_API_KEY=your-crux-api-key
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
```

## Running the Script

```bash
python scripts/cwv_report.py
```

* Upon success, the Good/NI/Poor percentages will be posted to Slack.

## GitHub Actions

The workflow `.github/workflows/crux_report.yml` ensures the script is executed daily.

```yaml
name: CrUX Web Vitals Report
on:
  schedule:
    - cron: '0 23 * * *'
  workflow_dispatch:

jobs:
  report:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v3
        with: { python-version: '3.10' }
      - run: pip install requests
      - run: python scripts/cwv_report.py
        env:
          CRUX_API_KEY: ${{ secrets.CRUX_API_KEY }}
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
          ORIGIN: "https://good-apps.jp"
```

## References

* Chrome UX Report API: https://developer.chrome.com/docs/crux/reference/api/
* Core Web Vitals Metrics Guide: https://support.google.com/webmasters/answer/9205520

## Licence

MIT Licence

      - run: pip install -r requirements.txt
      - run: python scripts/cwv_report.py
        env:
          CRUX_API_KEY: ${{ secrets.CRUX_API_KEY }}
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
          SLACK_CHANNEL_ID: ${{ secrets.SLACK_CHANNEL_ID }}
          SITEMAP_URL: ${{ secrets.SITEMAP_URL }}
```

## Customisation

* The `SITEMAP_URL` environment variable allows you to change the target sitemap.
* To adjust the observation period (in weeks), modify the `weeks_count` parameter.
* To change the graph type (line/bar) or colours, edit the matplotlib code.

## References

* Chrome UX Report API (History API): [https://developer.chrome.com/docs/crux/reference/history-api/records/queryHistoryRecord](https://developer.chrome.com/docs/crux/reference/history-api/records/queryHistoryRecord)
* Core Web Vitals Metrics Guide: [https://support.google.com/webmasters/answer/9205520](https://support.google.com/webmasters/answer/9205520)
* Slack API (files.upload): [https://api.slack.com/methods/files.upload](https://api.slack.com/methods/files.upload)

## Licence

MIT Licence
