# domstol.se monitor

Automated, 24/7 monitoring of Sveriges Domstolar news feeds (domstol.se).
For every new news item published by any Swedish court, the script:

1. Fetches the case page.
2. Asks Google Gemini (free tier) whether the case is in one of your areas of interest:
   - **A.** Public procurement (LOU, LUF, LUK, LUFS, LOV)
   - **B.** Commercial contract disputes (AB 04, ABT 06, ABK 09, etc.)
   - **C.** Access to public documents / secrecy re: public contracts and tenders
   - **D.** Highly noticed in Swedish media (only for lower-instance courts)
3. For lower-instance courts that did not hit A/B/C, checks Google News
   for recent Swedish coverage of the case.
4. If relevant, sends an HTML email to your inbox with a summary and a
   link straight to domstol.se.

Hosted on GitHub Actions — runs every 10 minutes, free, no server to maintain.
Realistic latency from publication to inbox: **10–20 minutes**.

---

## Quick setup (≈10 minutes)

### 1. Create the repo

1. Create a new private repo on GitHub (e.g. `domstol-monitor`).
2. Upload the contents of this folder into the repo root.
3. Confirm the file tree looks like this:

   ```
   .github/workflows/monitor.yml
   state/seen.json
   classifier.py
   feeds.py
   monitor.py
   news_check.py
   notifier.py
   requirements.txt
   .gitignore
   README.md
   ```

### 2. Get a Google Gemini API key (free)

1. Go to https://aistudio.google.com/ and sign in with a Google account.
2. Click **Get API key** (top-left) → **Create API key** → **Create API key in new project**.
3. Copy the key (starts with `AIza…`).

No credit card needed. The free tier gives you 15 requests/min and 1,500
requests/day on `gemini-2.5-flash`. The all-courts feed publishes roughly
1–10 items per day — well within the free quota.

### 3. Get an email-sending key

**Option A — Resend (recommended):**

1. Sign up at https://resend.com (free tier: 100 emails/day, 3,000/month).
2. API Keys → Create API Key. Copy the key (`re_…`).
3. Optionally verify your domain `riigalaw.se` (under Domains) so emails
   come from a real address. If you skip this, leave `RESEND_FROM` unset
   and Resend will send from `onboarding@resend.dev` (works, but may go
   to spam — verify the domain when you have a minute).

**Option B — SMTP via Microsoft 365 / Gmail:**

You'll need an app password (not your normal password). For M365 with MFA
this is `App passwords` in your security settings; for Gmail it's `App
passwords` once 2FA is enabled. Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
`SMTP_PASS`, `SMTP_FROM` (see step 4).

### 4. Add GitHub Actions secrets

In the GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**.

Required:

| Name | Value |
|---|---|
| `GEMINI_API_KEY` | Your Gemini key from step 2 |
| `NOTIFY_TO` | `peter@riigalaw.se` |

Pick one email backend:

**Resend:**

| Name | Value |
|---|---|
| `RESEND_API_KEY` | Your Resend key from step 3 |
| `RESEND_FROM` | Optional, e.g. `Domstolsbevakning <bevakning@riigalaw.se>` after domain verification |

**SMTP (instead of Resend):**

| Name | Value |
|---|---|
| `SMTP_HOST` | e.g. `smtp.office365.com` (M365) or `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | your full email address |
| `SMTP_PASS` | app password |
| `SMTP_FROM` | typically same as `SMTP_USER` |

### 5. First run — backfill, then enable

You almost certainly do NOT want a flood of emails covering every news
item already in the current feed (~20 items). Do this:

1. Go to **Actions → Monitor domstol.se → Run workflow**.
2. Set **backfill** to `true` and run.
3. The script will mark every current item as "seen" without classifying
   or emailing. State is committed back to the repo.

From then on, the scheduled cron (every 10 min) will only process new
items as they appear.

### 6. Optional: dry-run smoke test

To verify the classifier and email plumbing without flooding your inbox:

1. Make a tiny change to `state/seen.json` (remove one ID) and push.
2. Run the workflow with **dry_run** = `true`.
3. Check the Action logs for classifier verdicts. No email is sent.

---

## How the filter logic works

Pipeline per item, in order:

1. **Court lookup** from URL path → `(HFD, HD, KamR Sthlm, …, OTHER)`.
   `is_supreme = True` for HFD/HD, `False` otherwise.
2. **Page fetch + extract** → article title and body text.
3. **Gemini classifier** returns:
   `match` (bool), `categories` (A/B/C/D), `case_type`, `summary_sv`, `media_signal`.
4. Decision:
   - If `match == True` → **send email**.
   - Else if `is_supreme == False` and **Google News check** finds Swedish
     coverage of the case (by case number or title in the last 14 days) →
     send email with `Media: aktuell rapportering hittad`.
   - Else → skip.

For HFD/HD: per your spec, we skip the media check. Any A/B/C match goes
straight to email.

---

## Customising

- **Adjust the categories** by editing the prompt in
  `classifier.py` (`SYSTEM_PROMPT`). Keep the JSON schema unchanged.
- **Add or restrict courts** by editing `COURT_LOOKUP` in `feeds.py`.
  To monitor only HFD and HD, swap `ALL_COURTS_FEED` in `monitor.py` for
  one of `PER_COURT_FEEDS["HFD"]` / `["HD"]` (you'd need two feed pulls).
- **Change cadence** by editing `cron:` in `.github/workflows/monitor.yml`.
  GitHub's minimum is 5 minutes (`*/5 * * * *`) but in practice runs are
  delayed 5–15 min during peak load. 10 min is a fine default.
- **Tune the news-coverage window** with `RECENT_DAYS` in `news_check.py`.

---

## Operating notes

- **State** lives in `state/seen.json`. The workflow commits it back on
  every run. If the file gets large (≥ 5000 entries) we prune in place.
- **Idempotency**: failed runs do NOT mark items as seen, so they'll be
  retried next cron. Successful classification (match or no-match) is
  always recorded.
- **What if domstol.se changes their RSS URLs?** Check the "Aktuell
  filtrering med RSS" link on https://www.domstol.se/nyheter/ and replace
  `ALL_COURTS_FEED` in `feeds.py`.
- **What about avgöranden (rättspraxis.etjanst.domstol.se)?** The news
  feed almost always carries a notis for every published judgment and
  prövningstillstånd of public interest, with a link to the avgörande,
  so this script catches them. If you ever want the rättspraxis portal
  monitored separately, that's a different scraper — feed me a sample
  URL and we'll extend.

---

## Troubleshooting

**"No email backend configured"** in Action logs → add either `RESEND_API_KEY` or all five `SMTP_*` secrets.

**Emails in spam** → verify your sending domain in Resend, or set an SPF
record. With `onboarding@resend.dev` Gmail/M365 may quarantine.

**Action keeps failing on commit step** → ensure the workflow has
`permissions: contents: write` (it does, in the YAML) and your repo
hasn't restricted Actions write access (Settings → Actions → General).

**Too many false positives** → tighten the prompt in `classifier.py`.
You can also flip the order: require `match=True` AND 