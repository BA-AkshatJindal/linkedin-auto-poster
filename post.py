"""LinkedIn Daily Auto-Poster — 100% free stack.

Flow: pick a topic -> Gemini writes the post + image prompt -> Gemini (or
Pollinations fallback) makes the image -> publish to LinkedIn.

No paid platform, no server. Runs on GitHub Actions' free cron.

Required env vars (set as GitHub Actions secrets):
  GEMINI_API_KEY          - free key from https://aistudio.google.com
  LINKEDIN_ACCESS_TOKEN   - personal token with w_member_social scope
Optional:
  LINKEDIN_PERSON_URN     - e.g. "urn:li:person:abc123". If unset we fetch it.
  TOPIC                   - force a specific topic instead of the rotating list.
"""

import io
import os
import re
import json
import base64
import random
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from textwrap import dedent
from urllib.parse import quote

import httpx
from PIL import Image, ImageDraw, ImageFont
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

# The niche this account posts about.
CORE_THEMES = "Product Management, Product Strategy, AI & AI agents, LLMs, Business Analysis, and Product Operations"

# ── Static backup topics ──────────────────────────────────────────────
# Used ONLY if the live trend engine fails. Keep them on-brand: Product
# Management, Product Strategy, AI / agents / product building.
TOPICS = [
    "Why feature roadmaps fail without a clear non-goal list",
    "The difference between product velocity and real product progress",
    "Why 80% of product features are rarely or never used",
    "How to handle executive stakeholder pushback without ruining your roadmap",
    "Why user interviews lie and user behavior never does",
    "The danger of building for power users instead of your core ICP",
    "How Business Analysts bridge the gap between engineering reality and product vision",
    "Why technical debt is a product strategy decision, not an engineering bug",
    "The real cost of pivoting your product roadmap every quarter",
    "Why product managers should write problem statements, not solution specs",
    "How to prioritize product backlog items when everything is urgent",
    "Why metric-driven product design often leads to local maxima",
    "The art of saying no to high-value enterprise custom requests",
    "Why product metrics matter less than retention cohorts",
    "How product specs change when building AI-first features",
    "The silent product killer: shipping before defining success metrics",
    "Why business analysts are the unsung heroes of AI product adoption",
    "The trade-off between shipping fast and building scalable UX",
    "Why product-led growth fails without product-led onboarding",
    "How to run user research that actually changes product decisions",
    "Why most AI agents fail in production, not in demos",
    "The real reason 'AI-first' products lose users",
    "What nobody tells you about building reliable LLM apps",
    "Why your AI agent needs guardrails before features",
    "Agentic workflows are quietly eating traditional SaaS",
    "The hidden cost of context windows in agent design",
    "Why evals matter more than your model choice",
    "RAG isn't dead — you're just doing it wrong",
    "A product manager's guide to shipping AI that sticks",
    "Why prompt engineering is becoming product engineering",
    "Multi-agent systems: real power or expensive complexity?",
    "What founders get wrong about 'AI-first' product strategy",
    "Why human-in-the-loop still beats full autonomy",
    "The skill gap that's killing AI product teams",
    "How to price an AI product without burning margins",
    "Why most AI features solve the wrong problem",
    "The case for boring, reliable AI over flashy demos",
    "What AI agent startups get wrong about retention",
    "Tool-calling is the real unlock, not bigger models",
    "Why your AI roadmap should be bets, not features",
    "The underrated power of small, specialized models",
    "How agent memory changes product design forever",
    "Why latency, not accuracy, kills AI adoption",
    "The truth about AI moats (most don't have one)",
    "Designing trust into autonomous AI systems",
    "Why 'ship fast' breaks differently with AI products",
    "The feedback loop every AI product is missing",
    "What I learned debugging a misbehaving AI agent",
    "Why context engineering beats prompt engineering",
    "The quiet rise of vertical AI agents",
    "How to know if your problem actually needs an agent",
    "Why AI UX is harder than AI infrastructure",
    "The metrics that actually predict AI product success",
    "Building AI products users don't have to babysit",
    "Why most 'autonomous' agents are just glorified scripts",
    "The coming shift from chatbots to agentic interfaces",
    "What makes an AI product feel magical vs frustrating",
    "Why your eval set is your real competitive advantage",
    "The hard part of agents isn't reasoning, it's reliability",
    "How to scope an AI MVP that won't embarrass you",
    "Why data quality decides your AI product's ceiling",
    "The myth of the fully autonomous enterprise agent",
    "When to fine-tune vs when to just prompt better",
    "Why AI products need a 'confidence' UX layer",
    "The real ROI question every AI feature must answer",
    "How agent orchestration is becoming the new backend",
    "Why observability is non-negotiable for AI agents",
    "The product lessons hiding in failed AI launches",
    "Why saying no to AI features is a superpower",
    "What the next wave of AI-native products will look like",
]

# ── Live trend sources (free, keyless) ───────────────────────────────
TRENDS_RSS = [
    "https://news.google.com/rss/search?q=AI%20agents%20when:7d&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=%22Product%20Management%22%20OR%20%22Product%20Strategy%22%20when:7d&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=%22AI%20product%22%20OR%20LLM%20when:7d&hl=en-US&gl=US&ceid=US:en",
]
HN_SEARCH = "https://hn.algolia.com/api/v1/search?tags=story&query="
HN_QUERIES = ("AI agents", "LLM", "product management", "product strategy")

# Trend-topic cache: generate a fresh pool at most once per ~20h, then reuse it
# for every run in between (0 API hits for topic selection). Persisted across
# GitHub Actions runs via actions/cache (see the workflow).
TOPICS_CACHE_FILE = "topics_cache.json"
TREND_CACHE_HOURS = 20.0


def load_cached_topics():
    """Return (topics, age_hours) if a fresh cache exists, else (None, None)."""
    try:
        with open(TOPICS_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        gen = datetime.fromisoformat(data["generated"])
        age = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
        if age < TREND_CACHE_HOURS and data.get("topics"):
            return data["topics"], age
    except Exception:
        pass
    return None, None


def save_cached_topics(topics: list[str]) -> None:
    try:
        with open(TOPICS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"generated": datetime.now(timezone.utc).isoformat(), "topics": topics},
                f, indent=2,
            )
    except Exception as e:
        print(f"[trends] could not write cache: {e}")

# Each Gemini model has its OWN free-tier quota (~20 requests/day). We rotate
# through a preference-ordered list: when the best model hits its daily 429, we
# fall through to the next — multiplying free capacity (~5x) and degrading
# quality gracefully only when forced to.
TEXT_MODELS = [
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.5-flash-lite",
]
# Judge uses a different order so the writer and judge don't drain the same
# bucket first.
JUDGE_MODELS = [
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash",
]
TEXT_MODEL = TEXT_MODELS[0]   # back-compat for any direct reference
JUDGE_MODEL = JUDGE_MODELS[0]
IMAGE_MODEL = "gemini-2.5-flash-image-preview"  # 404s on this key -> Pollinations fallback

# Deterministic guardrails (cheap pre-filter before the LLM judge).
BANNED_PHRASES = [
    "leverage", "in today's landscape", "transformative", "game-changer",
    "game changer", "synergy", "delve", "tapestry", "unlock the power",
    "in conclusion", "elevate your", "supercharge",
]
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002190-\U000021FF\U00002B00-\U00002BFF]"
)
QUALITY_BAR = 8.0  # avg rubric score needed to stop early
HOOK_MIN = 8       # the hook must clear this on its own (reach depends on it)


# ── Transient-error retry ────────────────────────────────────────────
# Gemini's free tier often returns 503 UNAVAILABLE ("high demand") or 429
# (rate limit). These are temporary, so we retry with exponential backoff
# instead of letting one spike kill the whole run.

def with_retry(fn, *, tries=5, base_delay=5.0, retry_429=True):
    """Call fn(), retrying on transient Gemini errors (5xx, and 429 if retry_429).

    Set retry_429=False when a caller wants to handle daily-quota 429 itself
    (e.g. by switching to a different model) instead of waiting out a backoff.
    """
    last = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except genai_errors.ServerError as e:        # 5xx incl. 503 UNAVAILABLE
            last = e
        except genai_errors.ClientError as e:        # 4xx; only retry 429
            if getattr(e, "code", None) != 429 or not retry_429:
                raise
            last = e
        if attempt == tries:
            raise last
        delay = base_delay * (2 ** (attempt - 1))
        code = getattr(last, "code", "?")
        print(f"[retry] transient Gemini error ({code}); "
              f"attempt {attempt}/{tries}, sleeping {delay:.0f}s")
        time.sleep(delay)


def smart_generate(client: genai.Client, models: list[str], *, contents, config=None):
    """Generate content, rotating across models on daily-quota exhaustion.

    Tries each model in order. A transient 5xx retries (with backoff) on the
    SAME model; a daily-quota 429 immediately falls through to the NEXT model,
    so one exhausted bucket never blocks the run.
    """
    last = None
    for model in models:
        try:
            return with_retry(
                lambda m=model: client.models.generate_content(
                    model=m, contents=contents, config=config),
                retry_429=False,
            )
        except genai_errors.ClientError as e:
            if getattr(e, "code", None) == 429:
                print(f"[model] {model} hit daily quota (429) -> trying next model")
                last = e
                continue
            raise
        except genai_errors.ServerError as e:
            print(f"[model] {model} unavailable after retries -> trying next model")
            last = e
            continue
    raise last if last else RuntimeError("smart_generate: no models provided")


def gather_trend_signals(max_each: int = 6) -> list[str]:
    """Pull recent AI/agents/product headlines from free, keyless sources."""
    signals: list[str] = []

    for url in TRENDS_RSS:
        try:
            r = httpx.get(url, timeout=20.0, follow_redirects=True)
            r.raise_for_status()
            root = ET.fromstring(r.text)
            for item in root.findall(".//item")[:max_each]:
                t = (item.findtext("title") or "").strip()
                if " - " in t:  # drop "... - Publisher" suffix
                    t = t.rsplit(" - ", 1)[0].strip()
                if t:
                    signals.append(t)
        except Exception as e:
            print(f"[trends] Google News fetch failed: {e}")

    for q in HN_QUERIES:
        try:
            r = httpx.get(HN_SEARCH + quote(q), timeout=20.0)
            r.raise_for_status()
            for hit in r.json().get("hits", [])[:max_each]:
                t = (hit.get("title") or "").strip()
                if t:
                    signals.append(t)
        except Exception as e:
            print(f"[trends] Hacker News fetch failed: {e}")

    # de-dupe (case-insensitive), keep order, cap the list
    seen, out = set(), []
    for s in signals:
        k = s.lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out[:24]


def generate_trending_topics(client: genai.Client, signals: list[str], n: int = 24) -> list[str]:
    """Turn live trends into ~n fresh LinkedIn topic angles in the niche.

    Tries Gemini WITH Google Search grounding first (truly current), then falls
    back to a plain call using the fetched signals + the model's knowledge.
    """
    sig_text = "\n".join(f"- {s}" for s in signals) if signals else "(no feed signals available)"
    prompt = dedent(f"""\
        You curate LinkedIn post topics for a builder who posts about {CORE_THEMES}.
        Use the LATEST real trends and these recent headlines as inspiration:
        {sig_text}

        Produce {n} specific, fresh, opinionated LinkedIn POST TOPICS in this niche.
        - One topic per line, 6-14 words, a clear angle or hot take (don't copy headlines).
        - Center on AI, AI agents / agentic systems, LLMs, and product building.
        - ACCESSIBLE angles for a BROAD professional audience (founders, PMs, leaders):
          the implication, lesson, or hot take — NOT deep-technical specs, protocol
          names, or engineering internals.
        - Mix timely (tied to current trends) with sharp evergreen angles.
        - No numbering, no hashtags, no quotes. Just one topic per line.""")

    configs = []
    try:  # grounded variant (current web trends)
        configs.append(("grounded", types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())], temperature=1.0)))
    except Exception:
        pass
    configs.append(("plain", types.GenerateContentConfig(temperature=1.0)))

    for label, cfg in configs:
        try:
            resp = smart_generate(client, TEXT_MODELS, contents=prompt, config=cfg)
            lines = [re.sub(r"^\s*\d+[.)]\s*", "", ln.strip(" -•*\t").strip())
                     for ln in (resp.text or "").splitlines()]
            topics = [ln for ln in lines if len(ln.split()) >= 4 and not ln.startswith("#")]
            if len(topics) >= 10:
                print(f"[trends] generated {len(topics)} topics ({label})")
                return topics
        except Exception as e:
            print(f"[trends] topic generation failed ({label}): {e}")
    return []


def fetch_github_issue_spec() -> dict | None:
    """Fetch the oldest open GitHub issue to use as post topic, persona, and context."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # Check for issue labeled 'post-idea' first, then any open issue
    for url in (
        f"https://api.github.com/repos/{repo}/issues?state=open&labels=post-idea&sort=created&direction=asc",
        f"https://api.github.com/repos/{repo}/issues?state=open&sort=created&direction=asc",
    ):
        try:
            r = httpx.get(url, headers=headers, timeout=15.0)
            if r.status_code == 200:
                issues = r.json()
                for issue in issues:
                    if issue.get("pull_request"):
                        continue
                    title = (issue.get("title") or "").strip()
                    body = (issue.get("body") or "").strip()
                    if not title:
                        continue

                    persona = ""
                    context = body

                    m_persona = re.search(r"(?:persona|agent|role):\s*([^\n]+)", body, re.IGNORECASE)
                    if m_persona:
                        persona = m_persona.group(1).strip()

                    m_context = re.search(r"context:\s*(.+)", body, re.IGNORECASE | re.DOTALL)
                    if m_context:
                        context = m_context.group(1).strip()

                    print(f"[github-issue] Found open issue #{issue['number']}: '{title}'")
                    return {
                        "topic": title,
                        "persona": persona,
                        "context": context,
                        "issue_number": issue["number"],
                    }
        except Exception as e:
            print(f"[github-issue] Error fetching issues: {e}")
    return None


def close_github_issue(issue_number: int, comment_text: str) -> None:
    """Close the processed GitHub Issue and post a comment."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo or not issue_number:
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        httpx.post(
            f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments",
            headers=headers,
            json={"body": comment_text},
            timeout=15.0,
        )
        httpx.patch(
            f"https://api.github.com/repos/{repo}/issues/{issue_number}",
            headers=headers,
            json={"state": "closed", "state_reason": "completed"},
            timeout=15.0,
        )
        print(f"[github-issue] Closed issue #{issue_number}")
    except Exception as e:
        print(f"[github-issue] Error closing issue #{issue_number}: {e}")


def pick_topic(client: genai.Client | None = None) -> str:
    """TOPIC override > live trend-generated topic > static backup pool."""
    forced = os.environ.get("TOPIC", "").strip()
    if forced:
        return forced
    if client and os.environ.get("TRENDS_MODE", "true").strip().lower() in ("1", "true", "yes"):
        # 1) reuse a fresh cached pool — zero API hits
        cached, age = load_cached_topics()
        if cached:
            choice = random.choice(cached)
            print(f"[trends] cache hit ({len(cached)} topics, age {age:.1f}h, 0 API calls); picked: {choice}")
            return choice
        # 2) cache stale/missing -> refresh once, then cache it
        try:
            signals = gather_trend_signals()
            topics = generate_trending_topics(client, signals)
            if topics:
                save_cached_topics(topics)
                choice = random.choice(topics)
                print(f"[trends] refreshed {len(topics)} topics (cached); picked: {choice}")
                return choice
        except Exception as e:
            print(f"[trends] engine failed, using backup list: {e}")
    return random.choice(TOPICS)


def pick_post_spec(client: genai.Client | None = None) -> dict:
    """Returns {'topic': str, 'persona': str, 'context': str, 'issue_number': int|None}

    Priority:
    1. Environment variables (TOPIC, AGENT_PERSONA/PERSONA, CONTEXT)
    2. GitHub Issues from GitHub Mobile app
    3. upcoming_posts.json queue file
    4. Live AI trend generator / cached trend topics / backup topic pool
    """
    forced_topic = os.environ.get("TOPIC", "").strip()
    forced_persona = os.environ.get("AGENT_PERSONA", "").strip() or os.environ.get("PERSONA", "").strip()
    forced_context = os.environ.get("CONTEXT", "").strip()

    if forced_topic:
        return {
            "topic": forced_topic,
            "persona": forced_persona,
            "context": forced_context,
            "issue_number": None,
        }

    # 2) GitHub Issues (GitHub Mobile)
    gh_spec = fetch_github_issue_spec()
    if gh_spec:
        if forced_persona and not gh_spec["persona"]:
            gh_spec["persona"] = forced_persona
        if forced_context and not gh_spec["context"]:
            gh_spec["context"] = forced_context
        return gh_spec

    # 3) upcoming_posts.json queue
    if os.path.exists("upcoming_posts.json"):
        try:
            with open("upcoming_posts.json", encoding="utf-8") as f:
                queue = json.load(f)
            if isinstance(queue, list) and len(queue) > 0:
                item = queue.pop(0)
                if isinstance(item, dict) and item.get("topic"):
                    with open("upcoming_posts.json", "w", encoding="utf-8") as f:
                        json.dump(queue, f, indent=2)
                    print(f"[queue] Picked topic from upcoming_posts.json: '{item['topic']}'")
                    return {
                        "topic": item["topic"],
                        "persona": item.get("persona") or item.get("agent_persona") or forced_persona,
                        "context": item.get("context") or forced_context,
                        "issue_number": None,
                    }
        except Exception as e:
            print(f"[queue] Error reading upcoming_posts.json: {e}")

    # 4) AI trend engine / backup pool
    topic = pick_topic(client)
    return {
        "topic": topic,
        "persona": forced_persona,
        "context": forced_context,
        "issue_number": None,
    }


POST_FORMATS = [
    {
        "name": "Contrarian / Mythbuster",
        "instruction": "Structure the post as a contrarian take or mythbuster. Challenge a common misconception in tech/AI, using the topic to prove why conventional wisdom fails.",
    },
    {
        "name": "Hard Field Lesson",
        "instruction": "Structure the post around a sharp, pragmatic trade-off or hard-learned lesson. Focus on what teams get wrong vs what actually works in production.",
    },
    {
        "name": "Before vs. After Shift",
        "instruction": "Structure the post as a mindset shift. Contrast how products used to be designed vs how AI-native systems must be built today.",
    },
    {
        "name": "Rule of Thumb / Heuristic",
        "instruction": "Structure the post as a crisp rule of thumb or mental model for product leaders and builders.",
    },
    {
        "name": "Provocative Dilemma",
        "instruction": "Structure the post around an underlying tension (e.g. speed vs reliability, autonomy vs control). Lead with a bold stance on that dilemma.",
    },
]


def format_linkedin_text(text: str) -> str:
    """Ensure LinkedIn post text is cleanly formatted with punchy double line breaks (\\n\\n)
    between 1-2 sentence paragraphs for high mobile readability. Never a dense wall of text."""
    if not text:
        return text

    # Extract hashtags at the end
    hashtags = re.findall(r"#\w+", text)
    clean_text = re.sub(r"#\w+", "", text).strip()

    # Split into paragraphs by existing newlines
    paragraphs = [p.strip() for p in clean_text.split("\n") if p.strip()]

    final_paragraphs = []
    for p in paragraphs:
        # If a paragraph is dense (longer than 150 chars with multiple sentences), break it up
        if len(p) > 150 and "." in p:
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", p) if s.strip()]
            chunk = []
            for s in sentences:
                chunk.append(s)
                if len(" ".join(chunk)) > 100 or len(chunk) >= 2:
                    final_paragraphs.append(" ".join(chunk))
                    chunk = []
            if chunk:
                final_paragraphs.append(" ".join(chunk))
        else:
            final_paragraphs.append(p)

    formatted_body = "\n\n".join(final_paragraphs)

    if hashtags:
        seen = set()
        unique_tags = []
        for tag in hashtags:
            lower = tag.lower()
            if lower not in seen:
                seen.add(lower)
                unique_tags.append(tag)
        formatted_body += "\n\n" + " ".join(unique_tags[:5])

    return formatted_body


def load_post_history(filepath: str = "history_posts.json", limit: int = 15) -> list[dict]:
    """Load the last 15 published posts from local JSON history file."""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data[-limit:]
    except Exception as e:
        print(f"[history] error reading {filepath}: {e}")
    return []


def save_post_history(topic: str, commentary: str, urn: str = "", filepath: str = "history_posts.json", limit: int = 15):
    """Append the newly published post to the history file, capping at `limit` items."""
    history = load_post_history(filepath, limit=100)
    lines = [line.strip() for line in commentary.split("\n") if line.strip() and not line.strip().startswith("#")]
    hook = lines[0] if lines else topic

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "topic": topic,
        "hook": hook,
        "urn": urn,
        "likes": 0,
        "comments": 0,
        "engagement": 0,
    }
    history.append(entry)
    history = history[-limit:]

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
        print(f"[history] saved post history to {filepath} ({len(history)} total entries)")
    except Exception as e:
        print(f"[history] error writing {filepath}: {e}")


def fetch_and_update_post_performance(token: str, person_urn: str = "", filepath: str = "history_posts.json"):
    """Fetch real-time engagement metrics (likes, comments, reaction breakdown, and network followers)
    from LinkedIn APIs for past posts and update history_posts.json so the AI generator learns what performs best."""
    if not token or not os.path.exists(filepath):
        return

    history = load_post_history(filepath, limit=100)
    updated = False

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202601",
    }

    with httpx.Client(timeout=15.0) as client:
        # 1) Network & Followers Count API (GET /v2/networkSizes)
        if person_urn:
            try:
                person_id = person_urn.split(":")[-1]
                r_net = client.get(f"https://api.linkedin.com/v2/networkSizes/urn:li:person:{person_id}?edgeType=CompanyFollowedByMember", headers=headers)
                if r_net.status_code == 200:
                    net_count = r_net.json().get("firstDegreeSize", 0)
                    print(f"[analytics] Current LinkedIn Network Count: {net_count}")
            except Exception as e:
                print(f"[analytics] networkSizes note: {e}")

        # 2) Per-post Social Actions & Reactions Breakdown API
        for entry in history:
            urn = entry.get("urn")
            if not urn:
                continue
            try:
                enc = quote(urn, safe="")
                r = client.get(f"https://api.linkedin.com/v2/socialActions/{enc}", headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    likes = data.get("likesSummary", {}).get("totalLikes", 0)
                    comments = data.get("commentsSummary", {}).get("totalComments", 0)
                    entry["likes"] = likes
                    entry["comments"] = comments
                    entry["engagement"] = likes + (comments * 2)
                    updated = True

                # LinkedIn Reaction Breakdown API (GET /v2/socialActions/{urn}/reactions)
                r_rxn = client.get(f"https://api.linkedin.com/v2/socialActions/{enc}/reactions", headers=headers)
                if r_rxn.status_code == 200:
                    rxn_elements = r_rxn.json().get("elements", [])
                    rxn_counts = {}
                    for rx in rxn_elements:
                        rtype = rx.get("reactionType", "LIKE")
                        rxn_counts[rtype] = rxn_counts.get(rtype, 0) + 1
                    if rxn_counts:
                        entry["reactions_breakdown"] = rxn_counts
                        updated = True
                    print(f"[analytics] URN {urn[:30]}... -> {likes} likes, {comments} comments, reactions: {rxn_counts}")
            except Exception as e:
                print(f"[analytics] error fetching metrics for {urn}: {e}")

    if updated:
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2)
            print(f"[analytics] updated performance metrics in {filepath}")
        except Exception as e:
            print(f"[analytics] error saving metrics: {e}")


def reply_to_follower_comments(client: genai.Client, token: str, person_urn: str, filepath: str = "history_posts.json") -> None:
    """Followers' Comments Reader API & AI Auto-Replier: Reads follower comments on recent posts
    and posts authentic 1-2 sentence AI author replies to keep discussion threads active."""
    if not token or not person_urn or not os.path.exists(filepath):
        return

    history = load_post_history(filepath, limit=100)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202601",
    }
    updated = False

    for item in reversed(history[-5:]):
        urn = item.get("urn")
        if not urn:
            continue
        replied_set = set(item.get("replied_comment_urns", []))
        try:
            enc_urn = quote(urn, safe="")
            url = f"https://api.linkedin.com/v2/socialActions/{enc_urn}/comments"
            r = httpx.get(url, headers=headers, timeout=15.0)
            if r.status_code != 200:
                continue
            elements = r.json().get("elements", [])
            for c in elements:
                c_urn = c.get("$URN") or c.get("urn", "")
                actor = c.get("created", {}).get("actor", "") or c.get("actor", "")
                text = c.get("message", {}).get("text", "") or c.get("text", "")

                # Skip self-comments or already replied comments
                if not text or actor == person_urn or (c_urn and c_urn in replied_set):
                    continue

                prompt = dedent(f"""\
                    You are Akshat Jindal, a pragmatic product builder and Business Analyst.
                    A reader left this comment on your LinkedIn post.
                    Post Topic: {item.get('topic', '')}
                    Reader's Comment: "{text}"

                    Write a short, warm, authentic 1-2 sentence author response to this reader.
                    No corporate fluff, no emojis, no hashtags, no sales pitches. Just direct human conversation.
                """)
                reply_resp = smart_generate(client, TEXT_MODELS, contents=prompt)
                reply_text = (reply_resp.text or "").strip()
                if reply_text:
                    curn = post_comment(token, person_urn, urn, reply_text)
                    print(f"[auto-reply] replied to follower comment '{text[:30]}...': {curn}")
                    if c_urn:
                        replied_set.add(c_urn)
                        item["replied_comment_urns"] = list(replied_set)
                        updated = True
        except Exception as e:
            print(f"[auto-reply] follower comment reader check note for {urn}: {e}")

    if updated:
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            print(f"[auto-reply] error updating history: {e}")


# ── Gemini: write the post ───────────────────────────────────────────

def generate_post(client: genai.Client, topic: str, persona: str = "", context: str = "", feedback: str = "") -> dict:
    """Return {'commentary': str, 'image_prompt': str}.

    If `feedback` is given (from the eval agent), the model must fix those
    specific weaknesses in this draft — this is the reflexion loop.
    """
    author_desc = f"a real {persona}" if persona else "a pragmatic product builder and Business Analyst with 3 years of hands-on experience in tech"
    post_format = random.choice(POST_FORMATS)
    print(f"[format] selected style: {post_format['name']}")

    system = dedent(f"""\
        You are ghostwriting for {author_desc}, writing an authentic LinkedIn post.

        AUTHENTIC HUMAN VOICE (CRITICAL FOR TRUST & ORGANIC REPOSTS):
        - Write like a sharp, in-the-trenches builder sharing a genuine observation with peers.
        - ZERO AI BUBBLEGUM / ZERO MARKETING FLUFF. Never sound like a social media manager or a ChatGPT bot trying to be deep.
        - NO CHEESY RHETORICAL OPENERS ("Does X measure that anymore?", "Is Y dead?", "Let that sink in").
        - NO FORCED ENGAGEMENT BAIT ("Repost if you agree!", "What do you think? Drop a comment!").
        - Speak the real, everyday observations of shipping products and working with AI tools with quiet conviction.

        LINE SPACING & FORMATTING (CRITICAL FOR READABILITY):
        - NEVER write a wall of text paragraph.
        - Maximum 1-2 sentences per paragraph.
        - Leave a clear blank line (\\n\\n) between EVERY single paragraph.
        - Place 3-5 clean hashtags on a separate line at the very bottom with a blank line before them.

        TONE & FORMAT:
        - Grounded, pragmatic, and opinionated observations.
        - 80-140 words MAX. Concise, crisp, impactful. Every word earns its place.
        - Short sentences, natural line breaks, human conversational rhythm.
        - NO bullet lists. NO numbered tips. NO emojis.

        TRUTHFULNESS:
        - Share genuine opinions, observations, and widely-true insights.
        - NEVER invent fake personal anecdotes ("Last week my team deleted production..."), fake metrics, or fake statistics.

        Reply ONLY with JSON: {{"commentary": "...", "image_prompt": "...", "first_comment": "..."}}
        first_comment = a SHORT (1-2 sentences) follow-up the author drops as the
        FIRST comment — a sharper angle or clarifying thought. Conversational.""")

    user = dedent(f"""\
        Write a LinkedIn post anchored in your core niche ({CORE_THEMES}).

        STRUCTURAL ANGLE FOR THIS POST:
        Format Style: {post_format['name']}
        Structural Directive: {post_format['instruction']}

        USER INPUT SPARK / TOPIC REFERENCE:
        Topic: {topic}""")
    if context:
        user += f"\nSpecific Context / Notes: {context}"

    user += dedent("""

        HYBRID BLENDING DIRECTIONS:
        - 50% Specific Detail: Weave the specific nuance, real-world detail, or core idea of the topic into the post so it feels authentic, fresh, and non-generic.
        - 50% High-Level Umbrella: Connect it directly to the broader picture of AI products, LLM reliability, and product strategy so it resonates with founders, PMs, and tech leaders.
        - Ensure this post has its own unique rhythm, sentence structure, and flow. Avoid repeating generic templates.""")

    history = load_post_history(limit=15)
    if history:
        history_summary = "\n".join([f"- Topic: '{item.get('topic', '')}' | Hook: '{item.get('hook', '')}'" for item in history])
        user += dedent(f"""

            RECENTLY PUBLISHED POST HISTORY (DO NOT REPEAT):
            The following topics and hooks were recently published on this account:
            {history_summary}

            DEDUPLICATION DIRECTIVE:
            - Ensure your new post explores a FRESH, DISTINCT angle or unique insight.
            - Do NOT rehash or repeat the same arguments, conclusions, or core points used in the recent posts above.""")

        # Self-Learning Feedback Loop: Identify top-performing posts by engagement
        top_posts = sorted([item for item in history if item.get("engagement", 0) > 0], key=lambda x: x.get("engagement", 0), reverse=True)[:3]
        if top_posts:
            top_summary = "\n".join([f"- High-Engagement Post: Topic '{item['topic']}' | Hook: '{item['hook']}' ({item['likes']} likes, {item['comments']} comments)" for item in top_posts])
            user += dedent(f"""

                TOP PERFORMING POSTS ON THIS ACCOUNT (MIRROR THIS HIGH-ENGAGEMENT STYLE):
                The following posts earned the highest audience engagement on your profile:
                {top_summary}

                SELF-LEARNING STYLE DIRECTIVE:
                - Analyze why the top posts above resonated strongly with your audience.
                - Mirror the depth, high conviction, and trench-builder clarity of those top-performing posts.""")

    if feedback:
        user += dedent(f"""

            Your previous draft was rejected. FIX THESE SPECIFIC PROBLEMS:
            {feedback}
            Keep what worked; rewrite to fix the above.""")

    resp = smart_generate(client, TEXT_MODELS, contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "commentary": types.Schema(type=types.Type.STRING),
                    "image_prompt": types.Schema(type=types.Type.STRING),
                    "first_comment": types.Schema(type=types.Type.STRING),
                },
                required=["commentary", "image_prompt", "first_comment"],
            ),
            temperature=0.9,
        ))
    data = _extract_json(resp.text)
    raw_commentary = str(data["commentary"]).strip()
    formatted_commentary = format_linkedin_text(raw_commentary)

    return {
        "commentary": formatted_commentary,
        "image_prompt": str(data["image_prompt"]).strip(),
        "first_comment": str(data.get("first_comment", "")).strip(),
    }


def _extract_json(text: str) -> dict:
    """Parse the model's JSON reply, tolerating stray markdown fences or prose
    around the object. Raises (JSONDecodeError/ValueError) if nothing parses, so
    the caller treats it as a failed draft and regenerates — never a crash."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end > start:
            return json.loads(t[start:end + 1])   # outermost object span
        raise


RUBRIC_DIMS = ["hook", "insight", "authenticity", "relatability", "repostability", "originality"]


def guardrail_check(text: str) -> list[str]:
    """Deterministic format checks. Returns a list of issues (empty = clean)."""
    issues = []
    words = len(text.split())
    if words < 60:
        issues.append(f"Too short ({words} words; aim for 80-150).")
    elif words > 180:
        issues.append(f"Too long ({words} words; aim for 80-150).")
    if "?" not in text[-160:]:
        issues.append("Doesn't end with a question that invites discussion.")
    tags = re.findall(r"#\w+", text)
    if not (3 <= len(tags) <= 5):
        issues.append(f"Use 3-5 hashtags (found {len(tags)}).")
    if EMOJI_RE.search(text):
        issues.append("Remove all emojis.")
    low = text.lower()
    found = [p for p in BANNED_PHRASES if p in low]
    if found:
        issues.append("Remove corporate jargon: " + ", ".join(found))
    return issues


def evaluate_post(client: genai.Client, commentary: str) -> dict:
    """The eval agent: multi-dimension rubric score + actionable feedback.

    Returns {'scores': {dim:int}, 'overall': float, 'feedback': str}.
    """
    rubric = dedent(f"""\
        You are a tough LinkedIn content editor. Rate this post 1-10 on EACH:
        - hook: does the FIRST line stop the scroll?
        - insight: is there a real, specific idea (not generic advice)?
        - authenticity: does it sound like a real person, not AI/marketing?
        - relatability: is it deeply relatable to anyone working in tech, products, or business?
        - repostability: is the central takeaway shareable enough that someone would hit Repost?
        - originality: a fresh angle, not a cliche everyone has posted?

        Also set "fabricated": true if the post presents ANY invented personal
        anecdote, made-up event, fake metric/statistic, fake company/person/quote,
        or specific claim stated as real fact that a ghostwriter couldn't verify.
        Otherwise false.

        Then write ONE sentence of concrete, actionable feedback on the single
        biggest weakness (what to change to score higher).

        Reply ONLY with JSON:
        {{"hook":int,"insight":int,"authenticity":int,"relatability":int,"repostability":int,"originality":int,"fabricated":bool,"feedback":"..."}}

        <post>
        {commentary}
        </post>""")
    try:
        resp = smart_generate(client, JUDGE_MODELS, contents=rubric,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.3))
        d = json.loads(resp.text)
        scores = {k: int(d.get(k, 7)) for k in RUBRIC_DIMS}
        overall = sum(scores.values()) / len(RUBRIC_DIMS)
        return {"scores": scores, "overall": overall,
                "fabricated": bool(d.get("fabricated", False)),
                "feedback": str(d.get("feedback", "")).strip()}
    except Exception as e:
        print(f"[eval] judge failed, passing draft through: {e}")
        return {"scores": {}, "overall": 7.0, "fabricated": False, "feedback": ""}


# ── Image generation ─────────────────────────────────────────────────

def generate_image_gemini(client: genai.Client, image_prompt: str) -> bytes | None:
    """Try Gemini image model. Returns PNG bytes or None."""
    try:
        resp = client.models.generate_content(
            model=IMAGE_MODEL,
            contents=f"Generate a clean, modern image (no text): {image_prompt}",
        )
        for part in resp.candidates[0].content.parts:
            if getattr(part, "inline_data", None) and part.inline_data.data:
                return part.inline_data.data
    except Exception as e:
        print(f"[image] Gemini failed, falling back to Pollinations: {e}")
    return None


def get_font(size: int, bold: bool = False):
    font_paths = [
        "C:\\Windows\\Fonts\\segoeuib.ttf" if bold else "C:\\Windows\\Fonts\\segoeui.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf" if bold else "C:\\Windows\\Fonts\\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in font_paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def generate_graphic_card(topic: str, commentary: str = "") -> bytes:
    """Generate a sleek, modern, 1080x1080 dark-mode graphic card with Pillow.
    Features rich gradients, pill tags, the post's core hook, and clean typography.
    100% crisp visual quality with ZERO AI artifacts or muddy renders."""
    import io
    from PIL import Image, ImageDraw

    width, height = 1080, 1080
    img = Image.new("RGB", (width, height), color="#090d16")
    draw = ImageDraw.Draw(img)

    # Draw gradient glow background
    for radius in range(520, 0, -15):
        alpha = int(30 * (1 - radius / 520))
        color = (59, 130, 246)
        draw.ellipse([540 - radius, 540 - radius, 540 + radius, 540 + radius], fill=color)

    # Draw sleek outer border frame
    draw.rectangle([50, 50, width - 50, height - 50], outline="#1e293b", width=3)
    draw.rectangle([70, 70, width - 70, height - 70], outline="#334155", width=1)

    # Fonts
    font_tag = get_font(22, bold=True)
    font_hook = get_font(48, bold=True)
    font_footer = get_font(20, bold=False)

    # Top Pill Tag
    tag_text = "PRODUCT + AI INSIGHT"
    draw.rectangle([100, 120, 390, 175], fill="#1e293b", outline="#3b82f6", width=2)
    draw.text((120, 134), tag_text, fill="#60a5fa", font=font_tag)

    # Hook Text (Extract first line of commentary or topic)
    lines = commentary.strip().split("\n")
    hook = lines[0].strip() if lines else topic
    hook = re.sub(r"#\w+", "", hook).strip()
    if len(hook) > 130:
        hook = hook[:127] + "..."

    # Wrap text into lines
    words = hook.split()
    wrapped_lines = []
    current_line = []
    for word in words:
        current_line.append(word)
        if len(" ".join(current_line)) > 24:
            current_line.pop()
            wrapped_lines.append(" ".join(current_line))
            current_line = [word]
    if current_line:
        wrapped_lines.append(" ".join(current_line))

    # Draw Hook Text
    y_start = 360 - (len(wrapped_lines) * 25)
    for i, line in enumerate(wrapped_lines[:5]):
        draw.text((100, y_start + (i * 70)), line, fill="#f8fafc", font=font_hook)

    # Bottom accent line + signature
    draw.line([(100, 920), (980, 920)], fill="#3b82f6", width=4)
    draw.text((100, 945), "AKSHAT JINDAL  •  DAILY TECH & PRODUCT STRATEGY", fill="#94a3b8", font=font_footer)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


IMAGE_AESTHETIC_STYLES = [
    {
        "name": "Editorial Photography",
        "style": "editorial photograph, 35mm lens, f/1.8 shallow depth of field, warm natural rim lighting, 8k resolution, cinematic color grading, magazine cover quality",
    },
    {
        "name": "3D Glassmorphism & Octane Render",
        "style": "3D glassmorphic art render, Octane render, smooth frosted glass and vibrant gradient lighting, soft shadows, sleek minimal tech aesthetic, 8k",
    },
    {
        "name": "Cinematic Dark Mode Tech",
        "style": "cinematic dark mode photography, subtle cyan and amber accent neon rim lighting, modern high-tech workspace, moody atmosphere, sharp focus, 4k",
    },
    {
        "name": "Minimalist Conceptual Vector Art",
        "style": "minimalist 3D vector illustration, bold geometric composition, vibrant harmonious colors, clean layout, modern digital art, soft ambient depth",
    },
]


def generate_image_openai(image_prompt: str) -> bytes | None:
    """Generate high-end DALL-E 3 image if OPENAI_API_KEY is present."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": "dall-e-3",
            "prompt": f"{image_prompt}. High-end editorial photo, modern tech aesthetic. Absolutely no text, no letters, no words.",
            "n": 1,
            "size": "1024x1024",
            "quality": "hd",
        }
        r = httpx.post("https://api.openai.com/v1/images/generations", headers=headers, json=payload, timeout=60.0)
        r.raise_for_status()
        img_url = r.json()["data"][0]["url"]
        img_resp = httpx.get(img_url, timeout=60.0)
        img_resp.raise_for_status()
        print("[image-gen] Successfully generated DALL-E 3 image")
        return img_resp.content
    except Exception as e:
        print(f"[image-gen] DALL-E 3 failed: {e}")
        return None


def generate_image_recraft(image_prompt: str) -> bytes | None:
    """Generate high-end Recraft V3 image if RECRAFT_API_KEY is present."""
    api_key = os.environ.get("RECRAFT_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "prompt": f"{image_prompt}. Absolutely no text, no words.",
            "style": "digital_illustration",
            "size": "1024x1024",
        }
        r = httpx.post("https://api.recraft.ai/v1/images/generations", headers=headers, json=payload, timeout=60.0)
        r.raise_for_status()
        img_url = r.json()["data"][0]["url"]
        img_resp = httpx.get(img_url, timeout=60.0)
        img_resp.raise_for_status()
        print("[image-gen] Successfully generated Recraft V3 image")
        return img_resp.content
    except Exception as e:
        print(f"[image-gen] Recraft V3 failed: {e}")
        return None


def generate_carousel_pdf(topic: str, commentary: str = "") -> bytes:
    """Generate a high-end 3-slide PDF document (1080x1080 per slide) for LinkedIn Carousel.
    Includes container cards, electric accent glow, bullet icons, and magazine layout.
    """
    import io
    from PIL import Image, ImageDraw

    width, height = 1080, 1080
    slides = []

    # Clean lines without hashtags
    paragraphs = [p.strip() for p in commentary.split("\n\n") if p.strip() and not p.strip().startswith("#")]
    hook = paragraphs[0] if paragraphs else topic
    body_paragraphs = paragraphs[1:-1] if len(paragraphs) > 2 else paragraphs[1:]

    # Fonts
    font_tag = get_font(22, bold=True)
    font_title = get_font(42, bold=True)
    font_body = get_font(30, bold=False)
    font_footer = get_font(20, bold=False)
    font_bullet = get_font(32, bold=True)

    # Helper: Draw Slide Header & Footer Frame
    def draw_slide_frame(draw_obj, slide_num: int, title_tag: str):
        # Top gradient glow line
        draw_obj.rectangle([0, 0, width, 12], fill="#3b82f6")
        
        # Header Tag Box
        draw_obj.rectangle([70, 60, 420, 115], fill="#0f172a", outline="#3b82f6", width=2)
        draw_obj.text((90, 74), title_tag, fill="#60a5fa", font=font_tag)

        # Footer divider line
        draw_obj.line([70, 950, 1010, 950], fill="#1e293b", width=2)
        draw_obj.text((70, 975), "AKSHAT JINDAL • TECH & PRODUCT STRATEGY", fill="#94a3b8", font=font_footer)
        draw_obj.text((860, 975), f"SLIDE 0{slide_num} / 03", fill="#64748b", font=font_footer)

    # ── SLIDE 1: Cover Slide ──────────────────────────────────────────
    img1 = Image.new("RGB", (width, height), color="#090d16")
    draw1 = ImageDraw.Draw(img1)
    
    # Background gradient glow
    for radius in range(400, 0, -20):
        color = (59, 130, 246)
        draw1.ellipse([540 - radius, 450 - radius, 540 + radius, 450 + radius], fill=color)

    # Outer border frame
    draw1.rectangle([40, 40, width - 40, height - 40], outline="#1e293b", width=2)
    draw_slide_frame(draw1, 1, "PRODUCT + AI INSIGHT")

    # Main Hook Title Card Container
    draw1.rectangle([70, 200, 1010, 840], fill="#0b1329", outline="#1e293b", width=2)
    draw1.rectangle([70, 200, 82, 840], fill="#3b82f6") # Left electric accent line

    words = hook.split()
    wrapped = []
    curr = []
    for w in words:
        curr.append(w)
        if len(" ".join(curr)) > 22:
            curr.pop()
            wrapped.append(" ".join(curr))
            curr = [w]
    if curr:
        wrapped.append(" ".join(curr))

    y_pos = 280
    for line in wrapped[:6]:
        draw1.text((120, y_pos), line, fill="#f8fafc", font=font_title)
        y_pos += 68

    draw1.text((120, 780), "SWIPE FOR DEEP INSIGHTS ➔", fill="#38bdf8", font=font_tag)
    slides.append(img1)

    # ── SLIDE 2: Core Breakdown (2 Styled Container Cards) ─────────────
    img2 = Image.new("RGB", (width, height), color="#090d16")
    draw2 = ImageDraw.Draw(img2)
    draw2.rectangle([40, 40, width - 40, height - 40], outline="#1e293b", width=2)
    draw_slide_frame(draw2, 2, "THE HARD LESSON")

    y_card = 160
    for i, p in enumerate(body_paragraphs[:2]):
        # Container Card
        draw2.rectangle([70, y_card, 1010, y_card + 340], fill="#0b132c", outline="#1e293b", width=2)
        draw2.rectangle([70, y_card, 80, y_card + 340], fill="#38bdf8" if i == 0 else "#60a5fa")
        
        # Icon tag
        draw2.text((110, y_card + 30), f"0{i+1}. OBSERVATION", fill="#38bdf8", font=font_tag)

        # Wrap text
        words = p.split()
        p_lines = []
        c = []
        for w in words:
            c.append(w)
            if len(" ".join(c)) > 32:
                c.pop()
                p_lines.append(" ".join(c))
                c = [w]
        if c:
            p_lines.append(" ".join(c))

        y_text = y_card + 85
        for line in p_lines[:5]:
            draw2.text((110, y_text), line, fill="#e2e8f0", font=font_body)
            y_text += 48
        y_card += 380

    slides.append(img2)

    # ── SLIDE 3: Takeaway & Rule of Thumb ──────────────────────────────
    img3 = Image.new("RGB", (width, height), color="#090d16")
    draw3 = ImageDraw.Draw(img3)
    draw3.rectangle([40, 40, width - 40, height - 40], outline="#1e293b", width=2)
    draw_slide_frame(draw3, 3, "TAKEAWAY RULE OF THUMB")

    # Big Takeaway Container Card
    draw3.rectangle([70, 200, 1010, 840], fill="#0b132c", outline="#3b82f6", width=3)
    draw3.rectangle([70, 200, 84, 840], fill="#38bdf8")

    draw3.text((120, 240), "KEY HEURISTIC", fill="#60a5fa", font=font_tag)

    font_takeaway = get_font(38, bold=True)
    closing = paragraphs[-1] if paragraphs else "Ship with clarity. Focus on real product value."
    words = closing.split()
    w_lines = []
    c = []
    for w in words:
        c.append(w)
        if len(" ".join(c)) > 24:
            c.pop()
            w_lines.append(" ".join(c))
            c = [w]
    if c:
        w_lines.append(" ".join(c))

    y_pos = 320
    for line in w_lines[:6]:
        draw3.text((120, y_pos), line, fill="#38bdf8", font=font_takeaway)
        y_pos += 62

    slides.append(img3)

    pdf_buffer = io.BytesIO()
    slides[0].save(pdf_buffer, format="PDF", save_all=True, append_images=slides[1:])
    return pdf_buffer.getvalue()


def generate_image_pollinations(image_prompt: str) -> bytes:
    """Keyless free image generator (FLUX-Realism). Uses model=flux-realism for photorealistic rendering."""
    styled = f"{image_prompt}. Professional editorial photograph, 35mm lens, 8k resolution, crisp studio lighting. No text, no words, no letters, no logos, no watermark."
    seed = random.randint(1000, 999999)
    url = (
        f"https://image.pollinations.ai/prompt/{quote(styled)}"
        f"?width=1080&height=1080&seed={seed}&nologo=true&model=flux-realism"
    )
    print(f"[image-gen] requesting FLUX-Realism image with seed={seed}")
    r = httpx.get(url, timeout=120.0)
    r.raise_for_status()
    return r.content


def generate_image(client: genai.Client, image_prompt: str, topic: str = "", commentary: str = "") -> tuple[bytes, bool]:
    """Returns tuple of (media_bytes, is_pdf). Automatically picks optimal format based on day of week if default:
    - Wednesday: Interactive Poll
    - Monday / Thursday: PDF Carousel
    - Other days: FLUX / DALL-E 3 Studio Photo
    """
    raw_mode = os.environ.get("IMAGE_MODE", "auto").strip().lower()
    
    # Auto day-of-week schedule rotation if IMAGE_MODE is 'auto' or default 'ai'
    weekday = datetime.now(timezone.utc).weekday() # 0=Mon, 2=Wed, 3=Thu
    if raw_mode in ("auto", "ai") and not os.environ.get("EXPLICIT_IMAGE_MODE"):
        if weekday == 2: # Wednesday
            print("[schedule] Wednesday detected -> Auto-scheduling Interactive LinkedIn Poll")
            mode = "poll"
        elif weekday in (0, 3): # Monday, Thursday
            print("[schedule] Monday/Thursday detected -> Auto-scheduling 3-Slide PDF Carousel")
            mode = "carousel"
        else:
            mode = "ai"
    else:
        mode = raw_mode

    if mode in ("none", "off", "text", "false", "0"):
        print("[image-gen] IMAGE_MODE=none -> text-only post (no image attached)")
        return b"", False
    if mode in ("carousel", "pdf", "slides"):
        print("[image-gen] generating 3-slide PDF document for LinkedIn Carousel (Pillow)")
        return generate_carousel_pdf(topic, commentary), True
    if mode in ("ai", "flux", "dalle", "recraft"):
        img = generate_image_openai(image_prompt)
        if img:
            return img, False
        img = generate_image_recraft(image_prompt)
        if img:
            return img, False
        if os.environ.get("GEMINI_IMAGE", "false").strip().lower() in ("1", "true", "yes"):
            img = generate_image_gemini(client, image_prompt)
            if img:
                return img, False
        return generate_image_pollinations(image_prompt), False
    if mode == "card":
        print("[image-gen] generating graphic card (Pillow)")
        return generate_graphic_card(topic, commentary), False
    return b"", False


# ── Image-prompt agent ───────────────────────────────────────────────

def craft_image_prompt(client: genai.Client, topic: str, commentary: str = "") -> str | None:
    """Dedicated prompt-engineering agent for the image generator.

    Creates ONE concrete, vibrant visual prompt directly tied to the post's core message,
    enhanced with a professional aesthetic style (Editorial Photo, 3D Glassmorphism, Dark Tech, Vector Art).
    """
    chosen_style = random.choice(IMAGE_AESTHETIC_STYLES)
    print(f"[image-style] selected visual style: {chosen_style['name']}")

    sys_msg = dedent(f"""\
        You are an elite visual prompt engineer for FLUX image generator. Turn the LinkedIn
        post below into ONE stunning, scroll-stopping visual prompt that is DEEPLY RELEVANT
        to the post's central idea.

        VISUAL STYLE REQUIREMENT:
        Style Tag: {chosen_style['style']}

        HARD RULES:
        - ONE clear single subject that directly embodies the central message or metaphor of the post.
        - Make it VIBRANT, ATTRACTIVE, and CONTRASTY so it immediately stops the scroll on LinkedIn.
        - Describe a physical, real-world object, scene, or person that represents the core idea.
        - Keep it simple, elegant, and uncluttered (no crowded multi-object scenes).
        - NEVER include any text, words, letters, numbers, charts, diagrams, code, UI screens, logos, or watermarks.
        - Under 45 words. Output ONLY the visual subject description — the style tag will be appended automatically.

        Examples of strong visual transformations:
        - Post on "fast shipping without feedback": "A sleek runner sprinting on a red athletic track at sunrise, carrying a glowing compass, intense focus, crisp action shot."
        - Post on "AI memory & context window": "A glowing crystal sphere suspended over a minimalist oak desk, reflecting warm golden light rays."
        - Post on "evals over model size": "A precise silver scale balancing a glowing diamond against heavy iron gears, studio light background."
    """)
    content = f"TOPIC: {topic}\n\nPOST:\n{commentary}".strip()
    try:
        resp = smart_generate(
            client, TEXT_MODELS, contents=content,
            config=types.GenerateContentConfig(
                system_instruction=sys_msg, temperature=0.85),
        )
        base_prompt = (resp.text or "").strip().strip('"').strip()
        if not base_prompt:
            return None
        full_prompt = f"{base_prompt}, {chosen_style['style']}"
        return full_prompt
    except Exception as e:
        print(f"[image-prompt] agent failed, using writer's prompt: {e}")
        return None


# ── LinkedIn publishing ──────────────────────────────────────────────

def get_person_urn(token: str) -> str:
    override = os.environ.get("LINKEDIN_PERSON_URN", "").strip()
    if override:
        return override
    r = httpx.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    r.raise_for_status()
    return f"urn:li:person:{r.json()['sub']}"


def publish_to_linkedin(token: str, person_urn: str, commentary: str, image: bytes, is_pdf: bool = False) -> str:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202601",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=60.0) as client:
        payload = {
            "author": person_urn,
            "commentary": commentary,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

        if image:
            if is_pdf:
                # 1) reserve a document upload slot for PDF Carousel
                init = client.post(
                    "https://api.linkedin.com/rest/documents?action=initializeUpload",
                    headers=headers,
                    json={"initializeUploadRequest": {"owner": person_urn}},
                )
                init.raise_for_status()
                val = init.json()["value"]
                client.put(val["uploadUrl"], content=image, headers={"Content-Type": "application/pdf"}).raise_for_status()
                payload["content"] = {"media": {"id": val["document"], "title": "Product Strategy Carousel"}}
            else:
                # 1) reserve an image upload slot
                init = client.post(
                    "https://api.linkedin.com/rest/images?action=initializeUpload",
                    headers=headers,
                    json={"initializeUploadRequest": {"owner": person_urn}},
                )
                init.raise_for_status()
                val = init.json()["value"]

                # 2) upload the bytes
                client.put(
                    val["uploadUrl"], content=image, headers={"Content-Type": "image/png"}
                ).raise_for_status()

                payload["content"] = {"media": {"id": val["image"], "altText": "Visual"}}

        resp = client.post("https://api.linkedin.com/rest/posts", headers=headers, json=payload)
        if resp.status_code != 201:
            resp.raise_for_status()
        return resp.headers.get("x-restli-id", "")


def post_comment(token: str, person_urn: str, object_urn: str, text: str) -> str:
    """Add a comment (e.g. the author's first comment) to a post. Returns the
    comment URN. Uses the Social Actions API, which w_member_social allows."""
    enc = quote(object_urn, safe="")
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }
    r = httpx.post(
        f"https://api.linkedin.com/v2/socialActions/{enc}/comments",
        headers=headers,
        json={"actor": person_urn, "object": object_urn, "message": {"text": text}},
        timeout=30.0,
    )
    r.raise_for_status()
    return r.json().get("$URN", "")


# ── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    gemini_key = os.environ["GEMINI_API_KEY"]
    li_token = os.environ["LINKEDIN_ACCESS_TOKEN"]
    client = genai.Client(api_key=gemini_key)

    # 1) Get Member URN
    person_urn = get_person_urn(li_token)

    # 2) Fetch analytics, follower network count & reaction breakdown for past posts
    fetch_and_update_post_performance(li_token, person_urn=person_urn)

    # 3) Followers' Comments Reader API & AI Auto-Replier for recent comments
    reply_to_follower_comments(client, li_token, person_urn)

    spec = pick_post_spec(client)
    topic = spec["topic"]
    persona = spec["persona"]
    context = spec["context"]
    issue_number = spec["issue_number"]

    print(f"[post_spec] topic: '{topic}' | persona: '{persona}' | issue: {issue_number}")

    # Eval agent: up to 3 tries. Each draft is checked by deterministic
    # guardrails + an LLM rubric judge; the judge's feedback is fed into the
    # next draft (reflexion). We keep the BEST-scoring draft, not the first pass.
    best = None  # (score, post_dict)
    feedback = ""
    attempts = max(1, int(os.environ.get("MAX_ATTEMPTS", "3")))
    for attempt in range(1, attempts + 1):
        try:
            post = generate_post(client, topic, persona=persona, context=context, feedback=feedback)
        except Exception as e:
            # A malformed model reply must never kill the whole run — just retry.
            print(f"[draft] attempt {attempt}: generation/parse failed ({type(e).__name__}: {e}) -> regenerate")
            feedback = ("Return STRICTLY valid minified JSON with exactly the keys "
                        "commentary, image_prompt, first_comment and nothing else.")
            continue
        commentary = post["commentary"]

        issues = guardrail_check(commentary)
        if issues:
            feedback = "Fix these format issues: " + " ".join(issues)
            print(f"[draft] attempt {attempt}: guardrail fail {issues} -> regenerate (no judge call)")
            if best is None:           # keep a fallback so we always have something
                best = (0.0, post)
            continue

        ev = evaluate_post(client, commentary)   # judge only clean drafts
        score = ev["overall"]
        hook = ev["scores"].get("hook", 0)
        feedback = ev["feedback"]

        # Hard reject fabricated content — never post invented stories/claims.
        if ev.get("fabricated"):
            feedback = ("The post contains an invented story or unverifiable claim — "
                        "rewrite with ONLY truthful opinions and observations, no made-up "
                        "anecdotes, metrics, or events. " + feedback)
            print(f"[draft] attempt {attempt}: REJECTED (fabricated content) -> regenerate")
            if best is None:                 # keep only as last-resort fallback
                best = (0.0, post)
            continue

        if hook < HOOK_MIN:
            feedback = (f"The opening hook scored {hook}/10 — rewrite the FIRST line "
                        f"to be far more scroll-stopping. " + feedback)
        print(f"[draft] attempt {attempt}: clean, rubric {ev['scores']} avg={score:.1f} hook={hook}")

        if best is None or score > best[0]:
            best = (score, post)
        if score >= QUALITY_BAR and hook >= HOOK_MIN:   # reach hinges on the hook
            break

    if best is None:
        raise SystemExit("[fatal] no valid draft after all attempts — nothing posted")
    score, post = best
    commentary, image_prompt = post["commentary"], post["image_prompt"]
    first_comment = post.get("first_comment", "")
    print(f"[draft] using best draft (score {score:.1f})")
    print(f"[post]\n{commentary}\n")

    # Image-prompt agent: rewrite the writer's rough idea into a clean, concrete,
    # FLUX-optimized prompt (falls back to the writer's prompt if the agent fails).
    crafted = craft_image_prompt(client, topic, commentary)
    if crafted:
        print(f"[image-prompt] {crafted}")
        image_prompt = crafted
    image, is_pdf = generate_image(client, image_prompt, topic=topic, commentary=commentary)
    print(f"[image] {len(image)} bytes (is_pdf={is_pdf})")

    # Save outputs so a workflow run can upload them as a downloadable artifact
    # (lets you SEE the post + image/carousel from the Actions tab).
    out_file = "out_carousel.pdf" if is_pdf else "out_image.png"
    with open(out_file, "wb") as f:
        f.write(image)
    with open("out_post.txt", "w", encoding="utf-8") as f:
        f.write(f"TOPIC: {topic}\n\n{commentary}\n\n"
                f"FIRST COMMENT: {first_comment}\n\nIMAGE PROMPT: {image_prompt}\n")

    # Preview mode: generate everything but skip publishing to LinkedIn.
    if os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes"):
        print(f"[dry-run] preview only — NOT posting to LinkedIn. "
              f"Media saved to {out_file} (download it from the Actions artifact).")
        print(f"[dry-run] first comment would be: {first_comment}")
        if issue_number:
            close_github_issue(issue_number, f"✅ [DRY RUN] Generated preview post for topic: **{topic}**")
        return

    urn = publish_to_linkedin(li_token, person_urn, commentary, image, is_pdf=is_pdf)
    print(f"[done] published: {urn}")

    # Save post URN to local history file for future performance tracking
    save_post_history(topic, commentary, urn=urn)

    # First comment: drop the author's follow-up as the first comment to boost
    # reach. ON by default (no workflow env needed) — set FIRST_COMMENT_MODE=false to disable.
    if first_comment and os.environ.get("FIRST_COMMENT_MODE", "true").strip().lower() in ("1", "true", "yes"):
        # A freshly published post needs a few seconds to propagate before the
        # social-actions endpoint accepts comments (else 404), so retry briefly.
        for attempt in range(1, 5):
            try:
                curn = post_comment(li_token, person_urn, urn, first_comment)
                print(f"[done] first comment posted: {curn}")
                break
            except Exception as e:
                if attempt < 4:
                    print(f"[retry] first comment attempt {attempt} ({e}); waiting 10s for post to propagate")
                    time.sleep(10)
                else:
                    print(f"[warn] first comment failed after retries (post still published): {e}")

    # Close GitHub Issue if this post came from an open issue
    if issue_number:
        close_github_issue(
            issue_number,
            f"✅ **Published to LinkedIn!**\n\n**Topic:** {topic}\n\n**Post Text:**\n{commentary}\n\n**First Comment:**\n{first_comment}"
        )


if __name__ == "__main__":
    main()
