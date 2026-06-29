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

import os
import json
import base64
import random
import time
import xml.etree.ElementTree as ET
from textwrap import dedent
from urllib.parse import quote

import httpx
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

# The niche this account posts about.
CORE_THEMES = "AI, AI agents, agentic systems, LLMs, and product building"

# ── Static backup topics (50) ────────────────────────────────────────
# Used ONLY if the live trend engine fails. Keep them on-brand: AI / agents
# / products. The live engine (below) is what normally drives topics.
TOPICS = [
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
    "https://news.google.com/rss/search?q=%22AI%20product%22%20OR%20LLM%20when:7d&hl=en-US&gl=US&ceid=US:en",
]
HN_SEARCH = "https://hn.algolia.com/api/v1/search?tags=story&query="
HN_QUERIES = ("AI agents", "LLM", "AI product")

TEXT_MODEL = "gemini-2.5-flash"
IMAGE_MODEL = "gemini-2.5-flash-image-preview"


# ── Transient-error retry ────────────────────────────────────────────
# Gemini's free tier often returns 503 UNAVAILABLE ("high demand") or 429
# (rate limit). These are temporary, so we retry with exponential backoff
# instead of letting one spike kill the whole run.

def with_retry(fn, *, tries=5, base_delay=5.0):
    """Call fn(), retrying on transient Gemini errors (5xx / 429)."""
    last = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except genai_errors.ServerError as e:        # 5xx incl. 503 UNAVAILABLE
            last = e
        except genai_errors.ClientError as e:        # 4xx; only retry 429
            if getattr(e, "code", None) != 429:
                raise
            last = e
        if attempt == tries:
            raise last
        delay = base_delay * (2 ** (attempt - 1))
        code = getattr(last, "code", "?")
        print(f"[retry] transient Gemini error ({code}); "
              f"attempt {attempt}/{tries}, sleeping {delay:.0f}s")
        time.sleep(delay)


def gather_trend_signals(max_each: int = 10) -> list[str]:
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
    return out[:40]


def generate_trending_topics(client: genai.Client, signals: list[str], n: int = 50) -> list[str]:
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
            resp = with_retry(lambda: client.models.generate_content(
                model=TEXT_MODEL, contents=prompt, config=cfg))
            lines = [ln.strip(" -•\t").strip() for ln in (resp.text or "").splitlines()]
            topics = [ln for ln in lines if len(ln.split()) >= 4 and not ln.startswith("#")]
            if len(topics) >= 10:
                print(f"[trends] generated {len(topics)} topics ({label})")
                return topics
        except Exception as e:
            print(f"[trends] topic generation failed ({label}): {e}")
    return []


def pick_topic(client: genai.Client | None = None) -> str:
    """TOPIC override > live trend-generated topic > static backup pool."""
    forced = os.environ.get("TOPIC", "").strip()
    if forced:
        return forced
    if client and os.environ.get("TRENDS_MODE", "true").strip().lower() in ("1", "true", "yes"):
        try:
            signals = gather_trend_signals()
            topics = generate_trending_topics(client, signals)
            if topics:
                choice = random.choice(topics)
                print(f"[trends] picked: {choice}")
                return choice
        except Exception as e:
            print(f"[trends] engine failed, using backup list: {e}")
    return random.choice(TOPICS)


# ── Gemini: write the post ───────────────────────────────────────────

def generate_post(client: genai.Client, topic: str) -> dict:
    """Return {'commentary': str, 'image_prompt': str}."""
    system = dedent("""\
        You are ghostwriting LinkedIn posts for a real founder/BA who posts about Product + AI.

        RULES:
        - Write like a REAL PERSON, not a content marketer.
        - 80-150 words MAX. Short and punchy. Every word earns its place.
        - First person. Personal stories, hot takes, unpopular opinions.
        - Short sentences. Some one-liners. Break lines often.
        - NO corporate jargon ("in today's landscape", "transformative", "leverage").
        - NO bullet lists. NO numbered tips.
        - Start with a hook that stops the scroll.
        - End with a question that invites real discussion.
        - 2-4 hashtags at the very end. NO emojis.
        - Be opinionated. Take a stance.

        Reply ONLY with JSON: {"commentary": "...", "image_prompt": "..."}
        image_prompt = a VIVID, CONCRETE visual that captures the post's core idea
        or metaphor — a real scene, object, or moment a viewer instantly connects
        to the message (e.g. for "shipping fast without feedback" -> a runner
        sprinting blindfolded on a track). One clear subject, editorial and modern.
        NO text, words, letters, charts, graphs, or logos anywhere in the image.""")

    resp = with_retry(lambda: client.models.generate_content(
        model=TEXT_MODEL,
        contents=f"Write a LinkedIn post on: {topic}",
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0.9,
        ),
    ))
    data = json.loads(resp.text)
    return {"commentary": data["commentary"].strip(), "image_prompt": data["image_prompt"].strip()}


def evaluate_post(client: genai.Client, commentary: str) -> int:
    """Quick 1-10 quality score so we can retry a weak draft."""
    resp = with_retry(lambda: client.models.generate_content(
        model=TEXT_MODEL,
        contents=dedent(f"""\
            Rate this LinkedIn post 1-10 on hook, value, engagement, and how human it sounds.
            Reply ONLY with JSON: {{"score": <int>}}

            <post>
            {commentary}
            </post>"""),
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    ))
    try:
        return int(json.loads(resp.text)["score"])
    except Exception:
        return 7  # don't block publishing on a flaky eval


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


def generate_image_pollinations(image_prompt: str) -> bytes:
    """Keyless free image generator. Always works as a fallback."""
    styled = (
        f"{image_prompt}. Editorial photography, modern, cinematic, high detail, "
        "soft natural lighting, shallow depth of field. No text, no words, no logos."
    )
    url = (
        f"https://image.pollinations.ai/prompt/{quote(styled)}"
        "?width=1024&height=1024&nologo=true&model=flux&enhance=true"
    )
    r = httpx.get(url, timeout=120.0)
    r.raise_for_status()
    return r.content


def generate_image(client: genai.Client, image_prompt: str) -> bytes:
    return generate_image_gemini(client, image_prompt) or generate_image_pollinations(image_prompt)


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


def publish_to_linkedin(token: str, person_urn: str, commentary: str, image: bytes) -> str:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202601",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=60.0) as client:
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

        # 3) create the post
        resp = client.post(
            "https://api.linkedin.com/rest/posts",
            headers=headers,
            json={
                "author": person_urn,
                "commentary": commentary,
                "visibility": "PUBLIC",
                "distribution": {
                    "feedDistribution": "MAIN_FEED",
                    "targetEntities": [],
                    "thirdPartyDistributionChannels": [],
                },
                "content": {"media": {"id": val["image"], "altText": "AI-generated image"}},
                "lifecycleState": "PUBLISHED",
                "isReshareDisabledByAuthor": False,
            },
        )
        if resp.status_code != 201:
            resp.raise_for_status()
        return resp.headers.get("x-restli-id", "")


# ── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    gemini_key = os.environ["GEMINI_API_KEY"]
    li_token = os.environ["LINKEDIN_ACCESS_TOKEN"]
    client = genai.Client(api_key=gemini_key)

    topic = pick_topic(client)
    print(f"[topic] {topic}")

    # generate, with up to 3 tries to clear a quality bar
    commentary, image_prompt = "", ""
    for attempt in range(1, 4):
        post = generate_post(client, topic)
        commentary, image_prompt = post["commentary"], post["image_prompt"]
        score = evaluate_post(client, commentary)
        print(f"[draft] attempt {attempt}, score {score}/10")
        if score >= 7:
            break

    print(f"[post]\n{commentary}\n")

    image = generate_image(client, image_prompt)
    print(f"[image] {len(image)} bytes")

    # Save outputs so a workflow run can upload them as a downloadable artifact
    # (lets you SEE the post + image from the Actions tab).
    with open("out_image.png", "wb") as f:
        f.write(image)
    with open("out_post.txt", "w", encoding="utf-8") as f:
        f.write(f"TOPIC: {topic}\n\n{commentary}\n\nIMAGE PROMPT: {image_prompt}\n")

    # Preview mode: generate everything but skip publishing to LinkedIn.
    if os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes"):
        print("[dry-run] preview only — NOT posting to LinkedIn. "
              "Image saved to out_image.png (download it from the Actions artifact).")
        return

    person_urn = get_person_urn(li_token)
    urn = publish_to_linkedin(li_token, person_urn, commentary, image)
    print(f"[done] published: {urn}")


if __name__ == "__main__":
    main()
