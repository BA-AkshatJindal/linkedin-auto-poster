
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
