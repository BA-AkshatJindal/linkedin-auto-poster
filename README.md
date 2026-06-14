# LinkedIn Daily Auto-Poster (free)

Posts a daily AI-written LinkedIn post with a matching image. Runs on GitHub
Actions' free cron — your PC can be off. No paid platform, no server.

**Total cost: $0.** You only need free accounts: GitHub, Google AI Studio, and
a LinkedIn developer app.

## What it uses

| Piece          | Tool                        | Cost            |
|----------------|-----------------------------|-----------------|
| Hosting + cron | GitHub Actions              | Free            |
| Post text      | Google Gemini API           | Free tier       |
| Image          | Gemini, Pollinations.ai fallback | Free       |
| Topic          | Built-in rotating list (edit in `post.py`) | Free |
| Publishing     | LinkedIn REST API           | Free            |

> Your **Google AI Pro** subscription is for the Gemini *app*, not the API.
> The script uses a separate **free API key** from
> [aistudio.google.com](https://aistudio.google.com) — same Google account, no card.

---

## Setup (about 15 minutes, once)

### 1. Get a free Gemini API key
- Go to <https://aistudio.google.com> → **Get API key** → create one.
- Copy it. This is `GEMINI_API_KEY`.

### 2. Get your LinkedIn token
- Create an app at <https://www.linkedin.com/developers/apps>.
- In **Auth**, add redirect URL: `http://localhost:8765/callback`
- In **Products**, add **Share on LinkedIn** and **Sign In with LinkedIn using
  OpenID Connect** (both free).
- Copy the **Client ID** and **Client Secret** into the top of
  `get_linkedin_token.py` (or set them as env vars).
- Run it locally:
  ```
  pip install httpx
  python get_linkedin_token.py
  ```
- Click **Allow** in the browser. The terminal prints your
  `LINKEDIN_ACCESS_TOKEN` and `LINKEDIN_PERSON_URN`.
- ⚠️ This token expires in ~60 days. Re-run this script to refresh it.

### 3. Put the project on GitHub
- Create a new repo (private is fine) and upload these files.
- In the repo: **Settings → Secrets and variables → Actions → New repository
  secret**, add:
  - `GEMINI_API_KEY`
  - `LINKEDIN_ACCESS_TOKEN`
  - `LINKEDIN_PERSON_URN`

### 4. Turn it on
- Go to the **Actions** tab, enable workflows if prompted.
- Click **Daily LinkedIn Post → Run workflow** to test it now.
- After that it runs automatically every day at **06:30 UTC (12:00 PM IST)**.
  Change the time in `.github/workflows/daily-post.yml`.

---

## Customizing
- **Topics:** edit the `TOPICS` list in `post.py`, or force one with a `TOPIC`
  secret/env var.
- **Voice / length:** edit the `system` prompt in `generate_post`.
- **Time:** change the `cron:` line in the workflow file
  ([cron syntax](https://crontab.guru)).

## Test locally before pushing
```
pip install -r requirements.txt
# PowerShell:
$env:GEMINI_API_KEY="..."; $env:LINKEDIN_ACCESS_TOKEN="..."; python post.py
```

## Limitations (being honest)
- **LinkedIn token expires ~every 60 days** — re-run `get_linkedin_token.py`
  and update the secret. (No way around this without a paid backend to auto-refresh.)
- **GitHub cron can be delayed** a few minutes under load — normal, harmless.
- **Free Gemini tier has daily limits** — one post a day is far under them.
- Claude (your Pro plan) can't be the writer: it has no script/API access.
