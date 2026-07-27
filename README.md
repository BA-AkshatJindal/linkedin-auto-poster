# 🚀 Autonomous LinkedIn AI Growth & Content Engine

> **A Production-Grade, Autonomous Engine that drafts, evaluates, and publishes high-impact LinkedIn Posts, 3-Slide PDF Carousels, Interactive Polls, Real-Time News Reactions, and First Comments — 100% Hands-Free.**

[![GitHub License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Cost](https://img.shields.io/badge/Operating%20Cost-%240.00%2Fmo%20(Free%20Tier)-brightgreen.svg)]()
[![Automation](https://img.shields.io/badge/Execution-GitHub%20Actions%20(Twice%20Daily)-blueviolet.svg)]()
[![Quality Bar](https://img.shields.io/badge/Quality%20Control-6--Point%20AI%20Reflexion-orange.svg)]()

---

## 📖 The Story: Why This Engine Exists

Building a top 1% personal brand on LinkedIn is the single highest-ROI activity for product leaders, engineers, and founders. However, staying consistent comes with two major traps:

1. **The Time & Cost Sink:** Creating 1 high-impact post + visual carousel takes **15+ hours/week** or **$3,000+/month** hiring social media marketing agencies.
2. **The Generic AI Trap:** Most ChatGPT/AI auto-posters generate cringe, fluff-filled marketing text that ruins executive credibility and gets ignored by real practitioners.

**The Solution:** We built an **Autonomous AI Content Engine** calibrated to sound like an in-the-trenches practitioner (BA / Product / AI Strategy). It acts as a **Ghostwriter, Design Studio, and Quality Judge** — executing twice daily on cloud infrastructure for **$0/month**.

---

## 🖼️ Live Output & Visual Showcase

Here is a preview of the high-contrast, professional visual assets generated and published live by the engine:

| 📑 3-Slide Dark-Mode PDF Carousel | 📸 FLUX 8K Studio Photo |
| :---: | :---: |
| ![PDF Carousel Preview](out_card.png) | ![FLUX Studio Photo Preview](out_image.png) |
| *Rendered 1080x1080 `#0b132c` dark deck with electric glow & key heuristic badges* | *Photorealistic 35mm lens editorial photo generated via Pollinations.ai FLUX engine* |

---

## 📊 Client Case Study & ROI Metrics

> [!IMPORTANT]
> **Enterprise Efficiency Comparison:**  
> • **Traditional Marketing Agency:** $3,000 – $5,000 / month | Requires 3–5 hrs/week of review meetings  
> • **Autonomous AI Growth Engine:** **$0.00 / month running cost** | Requires **0 minutes / week** (100% Hands-Free)

```
 📈 System Performance Metrics:
 ├── Uptime: 99.9% Autonomous Execution
 ├── Content Formats: PDF Carousels, Native Polls, Studio PNGs, Quote Reshares
 ├── Quality Bar: 6-Point AI Reflexion Judge (Rejects marketing fluff)
 └── Multi-Model Redundancy: Gemini 2.5-Flash ➔ 2.0-Flash ➔ 1.5-Flash
```

---

## 🎨 End-to-End System Architecture & Data Flow

```
 ⏰ EXECUTED TWICE DAILY (09:30 AM & 07:30 PM IST via Cloud Workflows)
 └── Triggers GitHub Actions (on: workflow_dispatch)
```

```
 ┌────────────────────────────────────────────────────────┐
 │ 1. IDENTITY, FOLLOWERS & PERFORMANCE ANALYTICS         │
 │    • GET /v2/userinfo (Member URN)                     │
 │    • GET /v2/networkSizes (Audience Growth Tracking)   │
 │    • GET /v2/socialActions & /reactions (Analytics)    │
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │ 2. TOPIC DISCOVERY & 15-POST MEMORY ENGINE             │
 │    • Priority 1: Mobile Sparks & Quote Reshares        │
 │    • Priority 2: Real-time Google & Hacker News Trends │
 │    • Deduplication: Checks last 15 posts (zero repeats)│
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │ 3. AI GHOSTWRITER & 6-POINT QUALITY JUDGE              │
 │    • Human practitioner tone (BA / PM / AI / Freshers) │
 │    • Short 1-2 sentence paragraphs (\n\n formatting)   │
 │    • 6-point Gemini Reflexion Quality Judge            │
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │ 4. AUTOMATED DAY-OF-WEEK FORMAT SELECTOR               │
 │    ├─ 🗳️ Wed & Sun ➔ Interactive LinkedIn Polls        │
 │    ├─ 📑 Mon & Thu ➔ 3-Slide Dark-Mode PDF Carousels   │
 │    └─ 📸 Tue, Fri, Sat ➔ FLUX Studio Photos / Trends   │
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │ 5. LIVE PUBLISHING & ALGORITHM REACH BOOST             │
 │    • POST /rest/posts (Polls, Carousels, AI Photos)   │
 │    • POST /v2/socialActions (Author 1st comment boost)│
 └───────────────────────────┴────────────────────────────┘
```

---

## 🌟 Core Engine Capabilities

### 📑 1. 3-Slide Dark-Mode PDF Carousel Generator
* **Aesthetic:** Renders 1080x1080 high-contrast tech decks (`#0b132c` dark background, radial glow, numbered observation badges, and key heuristic takeaway callout).
* **Publishing:** Uses official LinkedIn Document Upload API (`/rest/documents?action=initializeUpload`).

### 🗳️ 2. Topic-Specific Interactive LinkedIn Polls
* **Dynamic Options:** Generates 2–4 unique, topic-specific voting choices (e.g. *"Feature velocity vs UX scaling"* instead of generic options).
* **Duration:** Native 3-day voting setting to maximize feed impressions.

### 🌐 3. Real-Time Industry Trend & News Reactions
* **Trend Engines:** Pulls live headlines from **Google News RSS** and **Hacker News Algolia API**.
* **Framing:** Synthesizes breaking tech news into sharp practitioner observations.

### 📱 4. Mobile Remote Control (GitHub Issues)
* **Custom Topic Sparks:** Type a topic title in GitHub Mobile ➔ Auto-publishes on next run.
* **LinkedIn Quote Reshares:** Paste a LinkedIn post URL ➔ Automatically drafts & publishes a **Quote Reshare** (`resharedShare`).

### ⚖️ 5. 6-Point Quality Judge & Reflexion Loop
Every draft is evaluated across 6 core dimensions (`hook`, `insight`, `authenticity`, `relatability`, `repostability`, `originality`). If a draft scores low or contains marketing fluff, the judge forces an immediate rewrite before publishing.

---

## 📅 Weekly Content Format Rotation

| Day of Week | Primary Media Format | Strategic Objective | Target Audience |
| :--- | :--- | :--- | :--- |
| **Monday** | 📑 **3-Slide PDF Carousel** | High-authority product framework deck | PMs, Leaders & Founders |
| **Tuesday** | 📸 **Studio AI Photo / Trend** | Real-time news reaction & BA insight | Tech Builders & Analysts |
| **Wednesday** | 🗳️ **Interactive LinkedIn Poll** | **Mid-week impression spike & quick votes** | All LinkedIn Feed Readers |
| **Thursday** | 📑 **3-Slide PDF Carousel** | Deep BA/PM trade-offs & mental models | Product Strategy Peers |
| **Friday** | 📸 **Studio AI Photo / Trend** | Industry week-in-review takeaway | Tech Practitioners |
| **Saturday** | 🎓 **Fresher & Aspirant Guide** | Junior BA & PM career mentoring | Students & Fresh Graduates |
| **Sunday** | 🗳️ **Interactive LinkedIn Poll** | **Casual Sunday morning voting & views** | Weekend Phone Browsers |

---

## 📱 Mobile Remote Controller Examples

You can trigger custom posts or Quote Reshares directly from **GitHub Mobile**:

- **Custom Topic Spark**:
  - **Issue Title**: `Why requirements elicitation beats prompt engineering`
  - **Issue Body**: `Context: Highlight why business analysis saves AI projects`

- **LinkedIn Quote Reshare (Repost with Thoughts)**:
  - **Issue Title**: `Reshare: https://www.linkedin.com/posts/username_activity-7123456789012345678`
  - **Issue Body**: `Context: Give my practitioner take on why their conclusion misses trade-offs`

The system automatically processes the issue, publishes to LinkedIn, and closes the issue!

---

## 🛠️ Stack & Free APIs

| Piece | API / Service | Cost |
| :--- | :--- | :--- |
| **Execution** | GitHub Actions (`workflow_dispatch`) | Free |
| **LinkedIn APIs** | Posts (`/rest/posts`), Media (`/rest/documents`, `/rest/images`), SocialActions (`/v2/socialActions`), UserInfo (`/v2/userinfo`), NetworkSizes (`/v2/networkSizes`) | Free |
| **AI Intelligence** | Google Gemini API (`gemini-2.5-flash`, `gemini-2.0-flash`) | Free Tier |
| **Image Generation** | Pollinations.ai (`model=flux-realism`), DALL-E 3 / Recraft V3 fallback | Free |
| **Trend Discovery** | Google News RSS & Hacker News Algolia API | Free |

---

## 💡 Deep-Dive Technical FAQ

<details>
<summary><b>1. How does the system guarantee zero post duplicates?</b></summary>
<br/>
The engine maintains a persistent `history_posts.json` cache on GitHub Actions. Every run inspects the last 15 published topics and hooks, automatically injecting a deduplication directive into Gemini's prompt to ensure fresh angles every time.
</details>

<details>
<summary><b>2. Is this compliant with LinkedIn Developer Policies?</b></summary>
<br/>
Yes, 100%. All posts, documents, images, and first comments are published via official LinkedIn REST API endpoints (v2 and RestLi 2.0). There is zero risky browser automation or headless scraping involved.
</details>

<details>
<summary><b>3. How does the multi-model failover work?</b></summary>
<br/>
Each Gemini model has its own daily free-tier quota. The script automatically iterates through `gemini-2.5-flash`, `gemini-2.0-flash`, and `gemini-1.5-flash`. If a model hits a 429 quota limit or 404, it instantly falls through to the next model seamlessly.
</details>

---

## 👤 Author & Architecture Contact

**Akshat Jindal**  
*Tech & Product Strategy | Automation Engineering*  
[LinkedIn Profile](https://www.linkedin.com/) • [GitHub Repository](https://github.com/BA-AkshatJindal/linkedin-auto-poster)
