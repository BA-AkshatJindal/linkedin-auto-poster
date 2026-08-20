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

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# The niche this account posts about.
CORE_THEMES = "Business Analysis, Product Management, AI & AI agents, Product Strategy, and Career Pathways in BA & PM for Freshers"

# ── Static backup topics ──────────────────────────────────────────────
# Used ONLY if the live trend engine fails. Keep them on-brand: Business
# Analysis, Product Management, AI / agents, and Career Advice for BA & PM Freshers.
TOPICS = [
    # ── Educational Frameworks & Teardowns (BABOK / Agile / Product) ──────
    "How to use the INVEST framework to write razor-sharp agile user stories",
    "BPMN 2.0 process mapping essentials: how junior BAs should map business workflows",
    "The MoSCoW prioritization method: how to prevent scope creep in sprint planning",
    "How to run a 5-Whys root cause analysis session with stubborn stakeholders",
    "Writing unambiguous Acceptance Criteria using Given-When-Then (Gherkin format)",
    "The Kano Model: how to categorize features into Basic, Performance, and Delighters",
    "Gap Analysis masterclass: mapping Current State to Future State step-by-step",
    "How to build a RACI matrix that eliminates team role confusion in projects",
    "Opportunity Solution Trees: how product teams connect user needs to real solutions",
    "Non-Functional Requirements (NFRs): the 5 categories every Business Analyst must document",
    "ICE vs RICE prioritization scoring: which framework fits your product stage?",
    "How to write a 1-page PRD that engineering teams actually love reading",
    "The difference between Outcome-Based Roadmaps and Feature Delivery Timelines",
    "North Star Metric decomposition: how to map high-level goals to input metrics",
    "The 4 essential UML diagrams every Business Analyst must master (Use Case, Activity, Sequence, State)",

    # ── AI Engineering & AI Product Architecture (Proof-backed) ───────────
    "Why eval-driven development beats prompt tweaking when building reliable AI products",
    "RAG vs Fine-Tuning: the 4-question decision tree for AI product builders",
    "How to design Human-in-the-Loop guardrails for autonomous AI agent workflows",
    "Why structured tool-calling beats open-ended text generation for AI reliability",
    "Chunking strategies in RAG pipelines: fixed-size vs semantic document splitting",
    "How to measure Precision vs Recall in production LLM applications",
    "Context window management: why stuffing 1M tokens often degrades reasoning quality",
    "Why golden test datasets are the only moat in enterprise generative AI products",
    "Building AI confidence scoring UX: how to show uncertainty without losing user trust",
    "Latency vs Cost vs Quality: the core trade-off triangle of production AI systems",
    "Agent memory architectures: short-term context vs persistent vector storage",
    "Why prompt engineering is evolving into deterministic workflow orchestration",
    "How to test LLM applications for edge cases before shipping to customers",

    # ── Fresher & Early Career Mastery (Actionable & High-Signal) ─────────
    "How freshers can build a high-signal BA portfolio project without prior work experience",
    "What hiring managers look for in a Business Analyst case study interview",
    "How to turn messy stakeholder meeting notes into a clean Functional Spec",
    "The 3 most common mistakes fresh graduates make in PM and BA technical interviews",
    "How junior BAs can lead requirement elicitation workshops with confidence",
    "Why understanding business domain logic matters 10x more than memorizing tools",
    "The non-linear pathway: transitioning from Junior Business Analyst to Product Manager",
    "How to build a product teardown portfolio that gets you noticed on LinkedIn",
    "3 core questions every junior BA should ask during initial stakeholder discovery",
    "How freshers can use AI tools as a learning copilot without hallucinating domain facts",

    # ── Real-World Product Strategy & Field Lessons ───────────────────────
    "Why feature roadmaps fail without an explicit Non-Goals section in the PRD",
    "The difference between product delivery velocity and actual customer value",
    "Why 80% of software features are rarely used and how to trim product bloat",
    "How Business Analysts bridge the communication gap between business logic and tech stacks",
    "The real cost of technical debt: why tech debt is a product strategy decision",
    "Why customer interviews lie and actual user telemetry never does",
    "The danger of building for loud vocal power users instead of your core ICP",
    "How to handle executive stakeholder pushback without blowing up sprint commitments",
    "Why product-led growth collapses without seamless product-led onboarding",
    "The silent product killer: shipping features before defining the telemetry metrics",
    "How to prioritize backlog items when engineering, sales, and executives disagree",
    "The art of saying no to custom enterprise feature requests without losing the account",
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
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]
# Judge uses a different order so the writer and judge don't drain the same
# bucket first.
JUDGE_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
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

    Tries each model in order. Transient errors or temporary 429 rate limits are
    retried with backoff. Non-429 client errors (like model 404s) or exhausted 429s
    gracefully fall through to the NEXT model.
    """
    last = None
    for model in models:
        try:
            return with_retry(
                lambda m=model: client.models.generate_content(
                    model=m, contents=contents, config=config),
                tries=3,
                base_delay=3.0,
                retry_429=True,
            )
        except genai_errors.ClientError as e:
            code = getattr(e, "code", None)
            if code == 429:
                print(f"[model] {model} hit daily quota (429) -> trying next model")
            else:
                print(f"[model] {model} ClientError ({code}: {e}) -> skipping model")
            last = e
            continue
        except genai_errors.ServerError as e:
            print(f"[model] {model} unavailable after retries -> trying next model")
            last = e
            continue
        except Exception as e:
            print(f"[model] {model} unexpected error ({e}) -> trying next model")
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


def extract_linkedin_urn(url_or_urn: str) -> str:
    """Extract urn:li:activity:12345 or urn:li:ugcPost:12345 from LinkedIn URL or string."""
    if not url_or_urn:
        return ""
    if "urn:li:" in url_or_urn:
        m = re.search(r"(urn:li:(?:activity|ugcPost|share):\d+)", url_or_urn)
        if m:
            return m.group(1)
    m = re.search(r"activity-(\d+)", url_or_urn)
    if m:
        return f"urn:li:activity:{m.group(1)}"
    m = re.search(r"(\d{18,20})", url_or_urn)
    if m:
        return f"urn:li:activity:{m.group(1)}"
    return ""


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
                    reshare_urn = ""

                    m_persona = re.search(r"(?:persona|agent|role):\s*([^\n]+)", body, re.IGNORECASE)
                    if m_persona:
                        persona = m_persona.group(1).strip()

                    m_context = re.search(r"context:\s*(.+)", body, re.IGNORECASE | re.DOTALL)
                    if m_context:
                        context = m_context.group(1).strip()

                    if title.lower().startswith("reshare:") or title.lower().startswith("repost:"):
                        reshare_urn = extract_linkedin_urn(title + " " + body)
                        print(f"[github-issue] Detected Reshare URN: {reshare_urn}")

                    print(f"[github-issue] Found open issue #{issue['number']}: '{title}'")
                    return {
                        "topic": title,
                        "persona": persona,
                        "context": context,
                        "issue_number": issue["number"],
                        "reshare_urn": reshare_urn,
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
        "name": "Educational Framework Breakdown",
        "instruction": (
            "Teach a proven, authentic industry framework (e.g. INVEST, MoSCoW, BPMN 2.0, Kano Model, RACI, 5-Whys, Opportunity Solution Tree). "
            "Structure: 1. The core challenge in teams ➔ 2. The Proven Framework with 3-4 clean structured bullets/points (e.g. • Letter/Step: meaning) ➔ 3. A crisp pro-tip or heuristic rule."
        ),
    },
    {
        "name": "Step-by-Step Actionable Blueprint",
        "instruction": (
            "Deliver a clear, practitioner how-to guide with concrete, actionable steps. "
            "Structure: 1. High-value objective ➔ 2. Clean numbered steps (1. [Action], 2. [Action], 3. [Action]) with specific details ➔ 3. Key takeaway or rule of thumb."
        ),
    },
    {
        "name": "Before vs. After (Amateur vs. Pro) Teardown",
        "instruction": (
            "Contrast common beginner mistakes with senior practitioner execution. "
            "Structure: 1. The core dilemma ➔ 2. 'Amateur approach:' vs 'Senior practitioner approach:' with clear contrast ➔ 3. Why this distinction decides project success."
        ),
    },
    {
        "name": "Decision Matrix & Heuristic Cheat Sheet",
        "instruction": (
            "Provide a crisp decision tree for choosing between architectural or product paths (e.g. RAG vs Fine-tuning, Agent vs Script, Build vs Buy). "
            "Structure: 1. The trade-off tension ➔ 2. Clean decision rules ('• If [Scenario] ➔ [Action]') ➔ 3. Strategic summary principle."
        ),
    },
    {
        "name": "Real-World Case Teardown (Proof-Backed)",
        "instruction": (
            "Analyze a well-documented, publicly verified industry case study (e.g. Stripe API design, Figma multiplayer architecture, Spotify squad evolution, Netflix chaos engineering). "
            "Strictly grounded in authentic facts without inventing numbers. Structure: 1. The context/problem ➔ 2. The specific decision made ➔ 3. The actionable takeaway for everyday builders."
        ),
    },
    {
        "name": "Contrarian Field Insight",
        "instruction": (
            "Challenge a common industry myth or vanity practice with a sharp, experienced-backed observation from the trenches. "
            "Structure: 1. Scroll-stopping counter-intuitive hook ➔ 2. Why conventional wisdom fails in production ➔ 3. The grounded alternative that actually delivers value."
        ),
    },
    {
        "name": "Fresher & Career Masterclass",
        "instruction": (
            "High-signal, practical career mentorship for aspiring BAs, PMs, and tech freshers. "
            "Structure: 1. The common resume/interview trap beginners fall into ➔ 2. The high-signal proof-of-work skill to build (with concrete artifacts like diagrams or PRDs) ➔ 3. Actionable drill to practice today."
        ),
    },
]


def format_linkedin_text(text: str) -> str:
    """Ensure LinkedIn post text is cleanly formatted with punchy spacing (\\n\\n)
    between logical paragraphs while keeping numbered steps and list items clean and readable."""
    if not text:
        return text

    # Extract hashtags at the end
    hashtags = re.findall(r"#\w+", text)
    clean_text = re.sub(r"#\w+", "", text).strip()

    # Split into raw lines / paragraphs by existing newlines
    raw_lines = [p.strip() for p in clean_text.split("\n") if p.strip()]

    final_blocks = []
    current_list_block = []

    for line in raw_lines:
        is_list_item = bool(re.match(r"^(\d+[.)]|•|-|➔|\*)\s+", line)) or line.startswith("Before:") or line.startswith("After:") or line.startswith("Amateur:") or line.startswith("Senior:")
        
        if is_list_item:
            current_list_block.append(line)
        else:
            if current_list_block:
                final_blocks.append("\n".join(current_list_block))
                current_list_block = []
            
            # If a paragraph is dense (longer than 180 chars with multiple sentences), break it up
            if len(line) > 180 and "." in line:
                sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", line) if s.strip()]
                chunk = []
                for s in sentences:
                    chunk.append(s)
                    if len(" ".join(chunk)) > 110 or len(chunk) >= 2:
                        final_blocks.append(" ".join(chunk))
                        chunk = []
                if chunk:
                    final_blocks.append(" ".join(chunk))
            else:
                final_blocks.append(line)

    if current_list_block:
        final_blocks.append("\n".join(current_list_block))

    formatted_body = "\n\n".join(final_blocks)

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


def get_recent_author_post_urns(token: str, person_urn: str, count: int = 10) -> list[str]:
    """Fetch recent post URNs directly from LinkedIn API to scan for follower comments."""
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202601",
    }
    urns = []
    try:
        url = f"https://api.linkedin.com/v2/shares?q=owners&owners={quote(person_urn, safe='')}&count={count}"
        r = httpx.get(url, headers=headers, timeout=15.0)
        if r.status_code == 200:
            for el in r.json().get("elements", []):
                u = str(el.get("id") or el.get("urn", ""))
                if u:
                    num = u.split(":")[-1]
                    if num.isdigit():
                        urns.append(f"urn:li:activity:{num}")
                    else:
                        urns.append(u)
    except Exception as e:
        print(f"[comments-fetch] shares query note: {e}")
    return urns


def reply_to_follower_comments(client: genai.Client, token: str, person_urn: str, filepath: str = "history_posts.json") -> None:
    """Followers' Comments Reader API & AI Auto-Replier: Reads follower comments on recent posts
    and posts authentic 1-2 sentence AI author replies to keep discussion threads active."""
    if not token or not person_urn:
        return

    history = load_post_history(filepath, limit=100) if os.path.exists(filepath) else []
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202601",
    }
    updated = False

    # Collect URNs from both history file and direct LinkedIn API query
    target_urns = []
    for item in reversed(history[-10:]):
        if item.get("urn"):
            target_urns.append(item["urn"])
    
    api_urns = get_recent_author_post_urns(token, person_urn, count=10)
    for u in api_urns:
        if u not in target_urns:
            target_urns.append(u)

    for urn in target_urns:
        # Find matching history item or create dummy tracking dict
        item = next((i for i in history if i.get("urn") == urn), {"urn": urn, "replied_comment_urns": []})
        replied_set = set(item.get("replied_comment_urns", []))
        try:
            enc_urn = quote(urn, safe="")
            url = f"https://api.linkedin.com/v2/socialActions/{enc_urn}/comments"
            r = httpx.get(url, headers=headers, timeout=15.0)
            if r.status_code != 200:
                print(f"[comments-scan] URN {urn[:35]}... status={r.status_code}: {r.text[:100]}")
                continue
            elements = r.json().get("elements", [])
            print(f"[comments-scan] URN {urn[:35]}... -> {len(elements)} comments found")
            for c in elements:
                c_urn = c.get("$URN") or c.get("urn", "")
                actor = str(c.get("actor", "") or c.get("created", {}).get("actor", ""))
                
                # Robust comment text extraction
                text = ""
                if isinstance(c.get("message"), dict):
                    text = c["message"].get("text", "")
                elif isinstance(c.get("message"), str):
                    text = c["message"]
                if not text:
                    text = c.get("text", "")

                print(f"[comments-scan] comment URN={c_urn} actor={actor} self={actor==person_urn} text='{text[:30]}'")

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
        You are ghostwriting for {author_desc}, writing an authentic, high-value LinkedIn post.

        CORE MISSION: TEACH & SHARE AUTHENTIC, VERIFIED KNOWLEDGE:
        - Deliver real educational value that teaches a specific skill, framework, decision tree, or operational lesson.
        - Ground every concept in recognized, verifiable industry standards (BABOK business analysis, Agile/Scrum practices, real product discovery, production AI engineering).
        - ZERO FLUFF / ZERO HAND-WAVING. Teach concrete steps, precise distinctions, and actionable rules.

        AUTHENTIC HUMAN VOICE:
        - Write like a sharp, in-the-trenches practitioner sharing a masterclass insight with peers.
        - ZERO AI BUBBLEGUM / ZERO MARKETING FLUFF. Never sound like an AI trying to sound inspirational or deep.
        - NO FORMULAIC OPENERS ("Does X matter anymore?", "Is Y dead?", "Let that sink in", "We need to talk about X").
        - NO FORCED ENGAGEMENT BAIT ("Repost if you agree!", "Drop a comment below!").
        - Speak with quiet conviction, precision, and clarity.

        FORMATTING & READABILITY:
        - Structure for effortless mobile readability.
        - When explaining steps, frameworks, or contrasts, use CLEAN list formatting (e.g. '1.', '2.', '3.' or '•' or 'Before:' / 'After:').
        - Keep regular paragraphs short (1-2 sentences) with clear blank lines (\\n\\n) between major sections.
        - Place 3-5 clean, relevant hashtags on a separate line at the very bottom with a blank line before them.

        LENGTH & CADENCE:
        - 90-200 words. Rich in insight, zero filler words.
        - Avoid repetitive rhythmic patterns. Vary sentence lengths naturally.
        - You may end with a punchy takeaway heuristic, an actionable pro-tip, a save-this-framework prompt, or an open thoughtful question. Do NOT force a generic question if a strong takeaway is more impactful.

        TRUTHFULNESS & ACCURACY (CRITICAL):
        - Share authentic, proof-backed methodologies and truthful observations.
        - NEVER invent fake personal anecdotes ("Last week my team did X..."), fake metrics ("boosted efficiency by 84.7%"), or fake company case studies.

        Reply ONLY with JSON: {{"commentary": "...", "image_prompt": "...", "first_comment": "..."}}
        first_comment = a SHORT (1-2 sentences) follow-up the author drops as the
        FIRST comment — an extra practical pro-tip, nuance, or clarifying resource hint.""")

    user = dedent(f"""\
        Write a LinkedIn post anchored in your core niche ({CORE_THEMES}).

        STRUCTURAL ARCHETYPE FOR THIS POST:
        Archetype Style: {post_format['name']}
        Structural Directive: {post_format['instruction']}

        TOPIC / KNOWLEDGE FOCUS:
        Topic: {topic}""")
    if context:
        user += f"\nSpecific Context / Notes: {context}"

    user += dedent("""

        TEACHING & DEPTH DIRECTIVES:
        - Deliver 1 concrete, actionable insight or framework rule that a reader can immediately apply in their BA / PM / AI workflow tomorrow.
        - Balance specific technical precision with executive clarity.
        - Avoid generic advice like 'communicate better' or 'align with stakeholders' — provide the exact HOW (e.g., using a RACI matrix, Gherkin criteria, or BPMN swimlanes).""")

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
            temperature=0.85,
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


RUBRIC_DIMS = ["hook", "insight", "educational_value", "authenticity", "repostability", "originality"]


def guardrail_check(text: str) -> list[str]:
    """Deterministic format checks. Returns a list of issues (empty = clean)."""
    issues = []
    words = len(text.split())
    if words < 70:
        issues.append(f"Too short ({words} words; aim for 90-200 to provide real educational depth).")
    elif words > 240:
        issues.append(f"Too long ({words} words; aim for 90-200).")
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
        You are a tough LinkedIn content editor and senior tech practitioner. Rate this post 1-10 on EACH:
        - hook: does the FIRST line immediately stop the scroll with an intriguing premise or observation?
        - insight: is there a sharp, non-obvious observation (not generic advice)?
        - educational_value: does it teach an authentic, actionable concept, framework, or practical rule?
        - authenticity: does it sound like a real practitioner, not AI marketing fluff?
        - repostability: is the central takeaway valuable enough that a professional would save or repost it?
        - originality: a fresh, non-cliche perspective or breakdown?

        Also set "fabricated": true if the post presents ANY invented personal
        anecdote ("my team deleted production"), fake metric/statistic ("boosted ROI by 82%"),
        fake company/quote, or unverified factual claims. Otherwise false.

        Then write ONE sentence of concrete, actionable feedback on the single
        biggest weakness (what to change to score higher).

        Reply ONLY with JSON:
        {{"hook":int,"insight":int,"educational_value":int,"authenticity":int,"repostability":int,"originality":int,"fabricated":bool,"feedback":"..."}}

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


def generate_image_pollinations(image_prompt: str) -> bytes | None:
    """Keyless free image generator (FLUX-Realism). Uses model=flux-realism for photorealistic rendering."""
    styled = f"{image_prompt}. Professional editorial photograph, 35mm lens, 8k resolution, crisp studio lighting. No text, no words, no letters, no logos, no watermark."
    seed = random.randint(1000, 999999)
    url = (
        f"https://image.pollinations.ai/prompt/{quote(styled)}"
        f"?width=1080&height=1080&seed={seed}&nologo=true&model=flux-realism"
    )
    print(f"[image-gen] requesting FLUX-Realism image with seed={seed}")
    for attempt in range(1, 3):
        try:
            r = httpx.get(url, timeout=120.0)
            r.raise_for_status()
            return r.content
        except Exception as e:
            print(f"[image-gen] Pollinations attempt {attempt} failed ({e})")
            if attempt < 2:
                time.sleep(2)
    return None


def generate_image(client: genai.Client, image_prompt: str, topic: str = "", commentary: str = "") -> tuple[bytes, bool]:
    """Returns tuple of (media_bytes, is_pdf). Automatically picks optimal format based on day of week if default:
    - Wednesday: Interactive Poll
    - Monday / Thursday: PDF Carousel
    - Other days: FLUX / DALL-E 3 Studio Photo
    """
    raw_mode = os.environ.get("IMAGE_MODE", "auto").strip().lower()
    
    # Auto day-of-week & morning/evening schedule rotation if IMAGE_MODE is 'auto' or 'ai'
    now_utc = datetime.now(timezone.utc)
    weekday = now_utc.weekday() # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    is_evening = now_utc.hour >= 10  # 07:30 PM IST = 14:00 UTC (is_evening = True)

    if raw_mode in ("auto", "ai") and not os.environ.get("EXPLICIT_IMAGE_MODE"):
        if weekday == 6: # Sunday
            if is_evening:
                print("[schedule] Sunday Evening detected -> Auto-scheduling Interactive Sunday Poll")
                mode = "poll"
            else:
                print("[schedule] Sunday Morning detected -> Auto-scheduling Studio AI Photo / Fresher Guide")
                mode = "ai"
        elif weekday == 2: # Wednesday
            if is_evening:
                print("[schedule] Wednesday Evening detected -> Auto-scheduling Interactive Wednesday Poll")
                mode = "poll"
            else:
                print("[schedule] Wednesday Morning detected -> Auto-scheduling 3-Slide PDF Carousel")
                mode = "carousel"
        elif weekday in (0, 3): # Monday, Thursday
            if (weekday == 0 and not is_evening) or (weekday == 3 and is_evening):
                print("[schedule] Mon Morn / Thu Eve detected -> Auto-scheduling 3-Slide PDF Carousel")
                mode = "carousel"
            else:
                mode = "ai"
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
        img = generate_image_pollinations(image_prompt)
        if img:
            return img, False
        print("[image-gen] AI image generator unavailable, falling back to local graphic card")
        return generate_graphic_card(topic, commentary), False
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


def generate_poll_data(client: genai.Client, topic: str, commentary: str = "") -> dict:
    """Generate a sharp, 140-char max poll question and 2-4 distinct options (under 30 chars each)
    for LinkedIn Polls API."""
    prompt = dedent(f"""\
        You are a LinkedIn content strategist. Create a sharp, debatable 1-question poll
        and 2-4 distinct voting options based on this specific post and topic:

        Topic: {topic}
        Post: {commentary}

        HARD RULES FOR POLL OPTIONS:
        - Question must be under 140 characters, highly relatable to tech/product builders.
        - Produce 2 to 4 options. Each option text MUST be under 30 characters MAX.
        - Options MUST be HIGHLY SPECIFIC to the topic above — NEVER use generic options like "Agree/Disagree", "Option A/Option B", or "Tool reliability/Team alignment".
        - Craft distinct real-world choices, trade-offs, or contrasting philosophies people actually debate.

        Reply ONLY with JSON:
        {{"question": "...", "options": ["Specific Choice 1", "Specific Choice 2", "Specific Choice 3"]}}
    """)
    try:
        resp = smart_generate(
            client, TEXT_MODELS, contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.9),
        )
        d = json.loads(resp.text)
        q = str(d.get("question", topic)).strip()[:140]
        opts = [str(o).strip()[:30] for o in d.get("options", []) if str(o).strip()]
        if len(opts) < 2:
            opts = [f"Focus on {topic[:15]}", "Classic Framework", "Hybrid Approach"]
        print(f"[poll-gen] generated custom poll: '{q}' | options: {opts[:4]}")
        return {"question": q, "options": opts[:4]}
    except Exception as e:
        print(f"[poll-gen] fallback poll data generation: {e}")
        # Dynamic fallback options based on topic keywords
        clean_topic = topic.split(":")[0][:20]
        return {
            "question": f"What is your biggest priority with {clean_topic}?",
            "options": [f"Speed in {clean_topic[:10]}", f"Quality of {clean_topic[:10]}", "Team Efficacy", "ROI & Business Metric"]
        }


def publish_to_linkedin(token: str, person_urn: str, commentary: str, image: bytes, is_pdf: bool = False, poll_data: dict | None = None, reshare_urn: str = "") -> str:
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

        if reshare_urn:
            payload["resharedShare"] = reshare_urn
            print(f"[linkedin-publish] Quote Resharing target URN: {reshare_urn}")
        elif poll_data:
            payload["content"] = {
                "poll": {
                    "question": poll_data["question"],
                    "options": [{"text": opt} for opt in poll_data["options"][:4]],
                    "settings": {
                        "duration": "THREE_DAYS"
                    }
                }
            }
        elif image:
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

    poll_data = None
    mode_check = os.environ.get("IMAGE_MODE", "").strip().lower()
    now_utc = datetime.now(timezone.utc)
    is_evening = now_utc.hour >= 10
    if mode_check == "poll" or (now_utc.weekday() in (2, 6) and is_evening and mode_check in ("", "auto", "ai")):
        poll_data = generate_poll_data(client, topic, commentary)

    reshare_urn = spec.get("reshare_urn") or os.environ.get("RESHARE_URN", "").strip()
    if reshare_urn:
        reshare_urn = extract_linkedin_urn(reshare_urn)

    urn = publish_to_linkedin(li_token, person_urn, commentary, image, is_pdf=is_pdf, poll_data=poll_data, reshare_urn=reshare_urn)
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
