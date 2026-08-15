"""One-click publisher for the custom Independence Day post.
Can run locally if LINKEDIN_ACCESS_TOKEN is set, or through GitHub Actions.
"""
import os
import sys
from urllib.parse import quote
import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def publish_custom():
    token = os.environ.get("LINKEDIN_ACCESS_TOKEN")
    if not token:
        print("ERROR: LINKEDIN_ACCESS_TOKEN environment variable not set.")
        sys.exit(1)

    with open("happy_independence_day_post.txt", "r", encoding="utf-8") as f:
        full_content = f.read()

    parts = full_content.split("---")
    post_text = parts[0].strip()
    first_comment = ""
    if len(parts) > 1 and "FIRST COMMENT:" in parts[1]:
        first_comment = parts[1].replace("FIRST COMMENT:", "").strip()

    # Get Person URN
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202601",
        "Content-Type": "application/json",
    }
    
    person_urn = os.environ.get("LINKEDIN_PERSON_URN", "").strip()
    if not person_urn:
        try:
            r = httpx.get("https://api.linkedin.com/v2/userinfo", headers={"Authorization": f"Bearer {token}"}, timeout=30.0)
            r.raise_for_status()
            person_urn = f"urn:li:person:{r.json()['sub']}"
        except Exception as e:
            print(f"[auth] failed to get userinfo: {e}")
            sys.exit(1)
            
    print(f"[auth] authenticated as {person_urn}")

    # Read image
    img_path = "happy_independence_day_2026.jpg"
    with open(img_path, "rb") as f:
        image_bytes = f.read()

    # Upload image
    with httpx.Client(timeout=60.0) as client:
        init = client.post(
            "https://api.linkedin.com/rest/images?action=initializeUpload",
            headers=headers,
            json={"initializeUploadRequest": {"owner": person_urn}},
        )
        init.raise_for_status()
        val = init.json()["value"]
        
        client.put(
            val["uploadUrl"], content=image_bytes, headers={"Content-Type": "image/jpeg"}
        ).raise_for_status()
        
        image_urn = val["image"]
        print(f"[media] uploaded image {image_urn}")

        # Post to feed
        payload = {
            "author": person_urn,
            "commentary": post_text,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
            "content": {"media": {"id": image_urn, "altText": "Happy Independence Day 2026"}}
        }

        resp = client.post("https://api.linkedin.com/rest/posts", headers=headers, json=payload)
        resp.raise_for_status()
        post_urn = resp.headers.get("x-restli-id", "")
        print(f"[publish] successfully published: {post_urn}")

        # Add first comment
        if first_comment and post_urn:
            try:
                enc = quote(post_urn, safe="")
                r_c = client.post(
                    f"https://api.linkedin.com/v2/socialActions/{enc}/comments",
                    headers={"Authorization": f"Bearer {token}", "X-Restli-Protocol-Version": "2.0.0", "Content-Type": "application/json"},
                    json={"actor": person_urn, "object": post_urn, "message": {"text": first_comment}},
                )
                print(f"[comment] posted first comment: {r_c.status_code}")
            except Exception as ce:
                print(f"[comment] first comment failed: {ce}")

if __name__ == "__main__":
    publish_custom()
