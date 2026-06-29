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

# ── Topic pool (edit freely) ─────────────────────────────────────────
# No paid "trending" API. We rotate through these. Add/remove any you like.
TOPICS = [
    "Why most AI features in products are solving the wrong problem",
    "The real cost of shipping fast without a feedback loop",
    "What founders get wrong about 'AI-first' product strategy",
    "Why your roadmap should be a list of bets, not features",
    "The underrated skill of saying no to good ideas",
    "How small teams out-ship big ones (and where they stall)",
    "Why 'we'll fix it later' is the most expensive sentence in tech",
    "The gap between a demo that wows and a product that lasts",
    "What I learned cutting a feature nobody used",
    "Why talking to 5 users beats reading 50 dashboards",
]

TEXT_MODEL = "gemini-2.5-flash"
IMAGE_MODEL = "gemini-2.5-flash-image-preview"

# ── World news source (free, keyless) ────────────────────────────────
# Google News RSS for the WORLD topic. NEWS_MODE (default on) makes each
# post react to a fresh, real headline instead of the static TOPICS pool.
NEWS_RSS = "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en"


def fetch_headlines(n: int = 12) -> list[str]:
    """Return up to n recent world headlines (titles), newest first."""
    r = httpx.get(NEWS_RSS, timeout=30.0, follow_redirects=True)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    heads = []
    for item in root.findall(".//item")[:n]:
        title = (item.findtext("title") or "").strip()
        # Google News titles end with " - Publisher"; drop that for a clean topic.
        if " - " in title:
            title = title.rsplit(" - ", 1)[0].strip()
        if title:
            heads.append(title)
    return heads


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


def pick_topic() -> str:
    """TOPIC override > a fresh world headline (NEWS_MODE) > static pool."""
    forced = os.environ.get("TOPIC", "").strip()
    if forced:
        return forced
    if os.environ.get("NEWS_MODE", "true").strip().lower() in ("1", "true", "yes"):
        try:
            heads = fetch_headlines()
            if heads:
                choice = random.choice(heads)
                print(f"[news] reacting to headline: {choice}")
                return choice
        except Exception as e:
            print(f"[news] fetch failed, using static topic list: {e}")
    return random.choice(TOPICS)


# ── Gemini: write the post ───────────────────────────────────────────

def generate_post(client: genai.Client, topic: str) -> dict:
    """Return {'commentary': str, 'image_prompt': str}."""
    system = dedent("""\
        You are ghostwriting LinkedIn posts for a sharp professional (AI / Product /
        Engineering background) who shares timely takes on world news and current affairs.

        You'll be given a real, recent news headline (or a topic). Write a thoughtful,
        professional reaction to it — your perspective, what it means, why it matters.

        RULES:
        - Write like a REAL PERSON, not a content marketer.
        - 80-150 words MAX. Short and punchy. Every word earns its place.
        - First person. A clear point of view.
        - Short sentences. Some one-liners. Break lines often.
        - NO corporate jargon ("in today's landscape", "transformative", "leverage").
        - NO bullet lists. NO numbered tips.
        - Start with a hook that stops the scroll.
        - End with a question that invites real discussion.
        - 2-4 hashtags at the very end. NO emojis.

        NEWS GUARDRAILS (important — this posts publicly):
        - Stay NON-PARTISAN. Do not take political sides or push an agenda.
        - For tragic, violent, or sensitive events: be measured, humane, and respectful.
          Never sensationalize, joke about, or exploit human suffering.
        - Connect the news to a broader professional/human lesson (leadership, technology,
          society, resilience, how we work) so it fits a professional audience.
        - If the headline is too graphic or purely partisan, pivot to the underlying
          theme rather than the inflammatory specifics.

        Reply ONLY with JSON: {"commentary": "...", "image_prompt": "..."}
        image_prompt = a VIVID, CONCRETE visual that captures the post's core idea
        or metaphor — a real scene, object, or moment a viewer instantly connects
        to the message (e.g. for "shipping fast without feedback" -> a runner
        sprinting blindfolded on a track). One clear subject, editorial and modern.
        NO text, words, letters, charts, graphs, or logos anywhere in the image.""")

    resp = with_retry(lambda: client.models.generate_content(
        model=TEXT_MODEL,
        contents=f"Write a LinkedIn post reacting to this news/topic: {topic}",
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

    topic = pick_topic()
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
