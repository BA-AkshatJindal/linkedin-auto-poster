# 🚀 LinkedIn Growth Auto-Poster Engine

An autonomous, 100% free system that drafts, evaluates, and publishes high-impact LinkedIn posts, 3-slide PDF carousels, interactive polls, real-time trend reactions, and AI-replies to follower comments.

---

## 🎨 System Architecture & Visual Execution Flow

```
 ⏰ EXECUTED TWICE DAILY (09:30 AM & 07:30 PM IST via External Trigger)
 └── Triggers GitHub Actions (on: workflow_dispatch)
```

### 1. Simple Visual Flowchart
```
 ┌────────────────────────────────────────────────────────┐
 │ 1. READ PROFILE, FOLLOWERS & ANALYTICS                 │
 │    • GET /v2/userinfo (Member URN)                     │
 │    • GET /v2/networkSizes (Follower Count Tracking)    │
 │    • GET /v2/socialActions & /reactions (Likes/Rxns)  │
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │ 2. AI FOLLOWERS COMMENT AUTO-REPLIER                   │
 │    • GET /v2/socialActions/{urn}/comments              │
 │    • Gemini drafts authentic 1-2 sentence replies      │
 │    • POST /v2/socialActions/{urn}/comments             │
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │ 3. TOPIC SELECTION & DEDUPLICATION                    │
 │    • Priority 1: GitHub Mobile Issues (sparks/reshares)│
 │    • Priority 2: Real-time Google & Hacker News Trends │
 │    • 15-Post Memory Engine: NEVER repeats previous topics│
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │ 4. AI GHOSTWRITER & 6-POINT QUALITY JUDGE              │
 │    • Human practitioner tone (BA / PM / AI / Freshers) │
 │    • 1-2 sentence short paragraphs (\n\n)              │
 │    • 6-point Gemini Reflexion Quality Judge            │
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │ 5. AUTOMATED DAY-OF-WEEK FORMAT SELECTOR               │
 │    ├─ 🗳️ Wed & Sun ➔ Interactive LinkedIn Polls        │
 │    ├─ 📑 Mon & Thu ➔ 3-Slide Dark-Mode PDF Carousels   │
 │    └─ 📸 Tue, Fri, Sat ➔ FLUX Studio Photos / Trends   │
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │ 6. PUBLISH TO LINKEDIN & ALGORITHM BOOST               │
 │    • POST /rest/posts (Polls, Carousels, AI Photos)   │
 │    • POST /v2/socialActions (Author 1st comment boost)│
 └────────────────────────────────────────────────────────┘
```

---

## 📅 Weekly Content Format Calendar

| Day of Week | Primary Media Format | Strategic Objective | Target Audience |
| :--- | :--- | :--- | :--- |
| **Monday** | 📑 **3-Slide PDF Carousel** | High-authority product framework deck | PMs, Leaders & Founders |
| **Tuesday** | 📸 **Studio AI Photo / Trend** | Real-time news reaction & BA insight | Tech Builders & Analysts |
| **Wednesday** | 🗳️ **Interactive LinkedIn Poll** | **Mid-week impression spike & quick votes** | All LinkedIn Feed Readers |
| **Thursday** | 📑 **3-Slide PDF Carousel** | Deep BA/PM trade-offs & mental models | Product Strategy Peers |
| **Friday** | 📸 **Studio AI Photo / Trend** | Industry week-in-review takeaway | Tech Practitioners |
| **Saturday** | 📸 **Fresher & Aspirant Guide** | Junior BA & PM career mentoring | Students & Fresh Graduates |
| **Sunday** | 🗳️ **Interactive LinkedIn Poll** | **Casual Sunday morning voting & views** | Weekend Phone Browsers |

---

## 📱 Mobile Remote Controller (GitHub Issues)

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
