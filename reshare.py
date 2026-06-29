"""Repost (reshare) an existing LinkedIn post WITH your own commentary.

The LinkedIn API does NOT let us read another post's text (403), so this tool
cannot "see" what it's resharing. You give it the post URL (for the URN) plus
either your exact note or the original text to react to.

Usage (local or via workflow_dispatch):
    # your own commentary, verbatim:
    REPOST_URL="https://www.linkedin.com/feed/update/urn:li:activity:123/" \\
    REPOST_NOTE="Spot on. The part about evals is exactly what teams miss." \\
    python reshare.py

    # let Gemini write a sharp take from the original text you paste:
    REPOST_URL="...123..." REPOST_CONTEXT="<paste the original post text>" python reshare.py

    # URL/URN can also be the first CLI arg:
    python reshare.py "https://www.linkedin.com/posts/..-activity-123-abcd"

Requires the same secrets as post.py: GEMINI_API_KEY (only if Gemini writes the
take), LINKEDIN_ACCESS_TOKEN, LINKEDIN_PERSON_URN.
"""

import os
import re
import sys

import httpx
from google import genai
from google.genai import types

import post  # reuse with_retry, get_person_urn, TEXT_MODEL


def extract_urn(value: str) -> str:
    """Pull a LinkedIn post URN out of a URL or accept a URN directly."""
    s = (value or "").strip()
    m = re.search(r"urn:li:(?:activity|share|ugcPost):\d+", s)
    if m:
        return m.group(0)
    # e.g. /feed/update/urn%3Ali%3Aactivity%3A123 or /posts/..-activity-123-abcd
    m = re.search(r"activity[:%\-_](?:[a-z0-9%]*?)(\d{8,})", s, re.IGNORECASE)
    if m:
        return f"urn:li:activity:{m.group(1)}"
    raise SystemExit(f"Could not find a LinkedIn post id in: {value!r}")


def write_take(client: genai.Client, context: str) -> str:
    """Gemini writes a short, thoughtful reshare commentary from given context."""
    sys_msg = (
        "You add a SHORT (2-4 sentences) thoughtful take when resharing a LinkedIn "
        "post. First person, a clear point of view, conversational, no fluff. "
        "End with a light question or call to engage. 1-2 hashtags max. No emojis."
    )
    resp = post.with_retry(lambda: client.models.generate_content(
        model=post.TEXT_MODEL,
        contents=f"Reshare this post. Add your take:\n\n{context}",
        config=types.GenerateContentConfig(system_instruction=sys_msg, temperature=0.9),
    ))
    return (resp.text or "").strip()


def reshare(token: str, person_urn: str, parent_urn: str, commentary: str) -> str:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202601",
        "Content-Type": "application/json",
    }
    body = {
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
        "reshareContext": {"parent": parent_urn},
    }
    r = httpx.post("https://api.linkedin.com/rest/posts", headers=headers, json=body, timeout=60.0)
    if r.status_code != 201:
        r.raise_for_status()
    return r.headers.get("x-restli-id", "")


def main() -> None:
    url = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("REPOST_URL", "")).strip()
    if not url:
        raise SystemExit("Provide a LinkedIn post URL via REPOST_URL or as the first argument.")
    parent_urn = extract_urn(url)
    print(f"[reshare] parent: {parent_urn}")

    note = os.environ.get("REPOST_NOTE", "").strip()
    context = os.environ.get("REPOST_CONTEXT", "").strip()
    if note:
        commentary = note
    elif context:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        commentary = write_take(client, context)
    else:
        raise SystemExit("Set REPOST_NOTE (your exact words) or REPOST_CONTEXT (original text to react to).")
    print(f"[reshare] commentary:\n{commentary}\n")

    if os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes"):
        print("[dry-run] not resharing.")
        return

    token = os.environ["LINKEDIN_ACCESS_TOKEN"]
    person_urn = post.get_person_urn(token)
    urn = reshare(token, person_urn, parent_urn, commentary)
    print(f"[done] reshared: {urn}")


if __name__ == "__main__":
    main()
