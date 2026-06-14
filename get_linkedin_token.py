"""One-time helper: get a LinkedIn access token on your own machine.

You only run this LOCALLY, once (and again every ~60 days when the token
expires). It opens LinkedIn in your browser, you click "Allow", and it prints
the access token + your person URN to paste into GitHub secrets.

SETUP (one time):
  1. Go to https://www.linkedin.com/developers/apps and create an app.
  2. In the app's "Auth" tab, add this Redirect URL:
        http://localhost:8765/callback
  3. In the "Products" tab, request "Share on LinkedIn" and "Sign In with
     LinkedIn using OpenID Connect" (both free, usually instant).
  4. Copy the Client ID and Client Secret into the two values below (or set
     them as env vars LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET).

Then run:   python get_linkedin_token.py
"""

import os
import sys
import webbrowser
from urllib.parse import urlencode, urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID", "PASTE_YOUR_CLIENT_ID")
CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "PASTE_YOUR_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8765/callback"
SCOPES = "openid profile w_member_social"

_auth_code = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if "code" in qs:
            _auth_code["code"] = qs["code"][0]
            self.wfile.write(b"<h2>Got it. You can close this tab and return to the terminal.</h2>")
        else:
            self.wfile.write(b"<h2>No code received. Check the terminal.</h2>")

    def log_message(self, *args):
        pass  # silence server logs


def main():
    if "PASTE_YOUR" in CLIENT_ID or "PASTE_YOUR" in CLIENT_SECRET:
        sys.exit("Edit CLIENT_ID and CLIENT_SECRET at the top of this file first (see the docstring).")

    auth_url = "https://www.linkedin.com/oauth/v2/authorization?" + urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
    })
    print("Opening LinkedIn in your browser. Click 'Allow'...")
    webbrowser.open(auth_url)

    # wait for the redirect to hit our tiny local server
    server = HTTPServer(("localhost", 8765), Handler)
    while "code" not in _auth_code:
        server.handle_request()

    # exchange the code for an access token
    token_resp = httpx.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "authorization_code",
            "code": _auth_code["code"],
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )
    token_resp.raise_for_status()
    access_token = token_resp.json()["access_token"]

    # fetch the person URN too, so you can store it as a secret
    me = httpx.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30.0,
    ).json()
    person_urn = f"urn:li:person:{me['sub']}"

    print("\n" + "=" * 60)
    print("Add these as GitHub Actions secrets:\n")
    print("LINKEDIN_ACCESS_TOKEN =")
    print(access_token)
    print("\nLINKEDIN_PERSON_URN =")
    print(person_urn)
    print("=" * 60)
    print("\nNote: this token expires in ~60 days. Re-run this script to refresh it.")


if __name__ == "__main__":
    main()
