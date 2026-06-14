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
from textwrap import dedent
from urllib.parse import quote

import httpx
from google import genai
from google.genai import types

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


def pick_topic() -> str:
    """Use TOPIC env override, else a random topic from the pool."""
    forced = os.environ.get("TOPIC", "").strip()
    if forced:
        return forced
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
        image_prompt = a brief description for a clean, modern visual. NO text in image.""")

    resp = client.models.generate_content(
        model=TEXT_MODEL,
        contents=f"Write a LinkedIn post on: {topic}",
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0.9,
        ),
    )
    data = json.loads(resp.text)
    return {"commentary": data["commentary"].strip(), "image_prompt": data["image_prompt"].strip()}


def evaluate_post(client: genai.Client, commentary: str) -> int:
    """Quick 1-10 quality score so we can retry a weak draft."""
    resp = client.models.generate_content(
        model=TEXT_MODEL,
        contents=dedent(f"""\
            Rate this LinkedIn post 1-10 on hook, value, engagement, and how human it sounds.
            Reply ONLY with JSON: {{"score": <int>}}

            <post>
            {commentary}
            </post>"""),
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
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
    url = (
        f"https://image.pollinations.ai/prompt/{quote(image_prompt)}"
        "?width=1024&height=1024&nologo=true"
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

    person_urn = get_person_urn(li_token)
    urn = publish_to_linkedin(li_token, person_urn, commentary, image)
    print(f"[done] published: {urn}")


if __name__ == "__main__":
    main()
