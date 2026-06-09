"""
==================================================================================
  MODULE 1 — Authentication (auth.py)
==================================================================================

  Daily login flow for Zerodha Kite Connect.
  
  USAGE:
    python auth.py
    
  FLOW:
    1. Opens (prints) the Kite login URL in your console.
    2. You open that URL in your browser, log in with your Zerodha credentials.
    3. Kite redirects to http://127.0.0.1?request_token=XXXX&action=login
       (this page will fail to load — that's expected).
    4. Copy the full redirect URL or just the request_token value.
    5. Paste it into the console when prompted.
    6. The script extracts the request_token, generates an access_token,
       and saves it to access_token.json for other modules to use.
       
  NOTE: The access_token is valid until ~6:00 AM the next morning.
        Run this script once each trading day before market open.
==================================================================================
"""

import json
import os
import sys
import re
from datetime import datetime
from urllib.parse import urlparse, parse_qs

from kiteconnect import KiteConnect

# Import configuration
from config import (
    KITE_API_KEY,
    KITE_API_SECRET,
    ACCESS_TOKEN_FILE,
)


def get_login_url() -> str:
    """Generate the Kite Connect login URL."""
    kite = KiteConnect(api_key=KITE_API_KEY)
    login_url = kite.login_url()
    return login_url


def extract_request_token(user_input: str) -> str:
    """
    Extract the request_token from user input.
    
    Accepts either:
      - The full redirect URL: http://127.0.0.1/?request_token=XXXX&action=login
      - Just the raw token string: XXXX
    
    Returns:
        The extracted request_token string.
        
    Raises:
        ValueError: If the token cannot be extracted.
    """
    user_input = user_input.strip()
    
    # Case 1: User pasted a full URL
    if user_input.startswith("http"):
        parsed = urlparse(user_input)
        params = parse_qs(parsed.query)
        tokens = params.get("request_token", [])
        if tokens:
            return tokens[0]
        raise ValueError(
            "Could not find 'request_token' parameter in the URL. "
            "Make sure the URL contains ?request_token=XXXX"
        )
    
    # Case 2: User pasted raw token (alphanumeric string)
    # Kite request tokens are typically alphanumeric, ~32 characters
    token = user_input.strip()
    if re.match(r'^[a-zA-Z0-9]+$', token) and len(token) > 8:
        return token
    
    raise ValueError(
        f"Invalid input: '{user_input[:50]}...'. "
        "Please paste either the full redirect URL or just the request_token value."
    )


def generate_access_token(request_token: str) -> dict:
    """
    Exchange the request_token for a session (access_token + user info).
    
    Args:
        request_token: The one-time token from the login redirect.
        
    Returns:
        dict with keys: access_token, user_id, login_time, etc.
        
    Raises:
        Exception: If the Kite API rejects the token (expired, already used, etc.)
    """
    kite = KiteConnect(api_key=KITE_API_KEY)
    
    try:
        session = kite.generate_session(request_token, api_secret=KITE_API_SECRET)
    except Exception as e:
        raise RuntimeError(
            f"Failed to generate access token: {e}\n"
            "Common causes:\n"
            "  - The request_token was already used (tokens are single-use)\n"
            "  - The request_token expired (they expire in ~60 seconds)\n"
            "  - API key/secret mismatch in config.py\n"
            "Solution: Re-run this script and complete the login quickly."
        ) from e
    
    return session


def save_access_token(session: dict) -> None:
    """
    Persist the access_token and metadata to a local JSON file.
    
    The file is written atomically (write to temp, then rename) to prevent
    corruption if another module reads it mid-write.
    """
    token_data = {
        "access_token": session["access_token"],
        "user_id": session.get("user_id", ""),
        "login_time": datetime.now().isoformat(),
        "api_key": KITE_API_KEY,
    }
    
    temp_file = ACCESS_TOKEN_FILE + ".tmp"
    with open(temp_file, "w") as f:
        json.dump(token_data, f, indent=2)
    
    # Atomic rename (works on Windows if target doesn't exist)
    if os.path.exists(ACCESS_TOKEN_FILE):
        os.remove(ACCESS_TOKEN_FILE)
    os.rename(temp_file, ACCESS_TOKEN_FILE)
    
    print(f"✅ Access token saved to: {os.path.abspath(ACCESS_TOKEN_FILE)}")


def load_access_token() -> dict:
    """
    Load the saved access_token from disk.
    
    Returns:
        dict with 'access_token', 'api_key', 'user_id', 'login_time'.
        
    Raises:
        FileNotFoundError: If the token file doesn't exist (need to run auth.py).
        json.JSONDecodeError: If the file is corrupted.
    """
    if not os.path.exists(ACCESS_TOKEN_FILE):
        raise FileNotFoundError(
            f"Token file '{ACCESS_TOKEN_FILE}' not found.\n"
            "Run 'python auth.py' first to generate your daily access token."
        )
    
    with open(ACCESS_TOKEN_FILE, "r") as f:
        data = json.load(f)
    
    # Sanity check
    if "access_token" not in data or "api_key" not in data:
        raise ValueError(
            f"Token file '{ACCESS_TOKEN_FILE}' is malformed. "
            "Delete it and run 'python auth.py' again."
        )
    
    return data


def get_authenticated_kite() -> KiteConnect:
    """
    Convenience function: load saved token and return a ready-to-use KiteConnect instance.
    
    Usage in other modules:
        from auth import get_authenticated_kite
        kite = get_authenticated_kite()
        # kite is now authenticated and ready for API calls
    """
    token_data = load_access_token()
    kite = KiteConnect(api_key=token_data["api_key"])
    kite.set_access_token(token_data["access_token"])
    return kite


# ──────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 70)
    print("  ZERODHA KITE CONNECT — DAILY LOGIN")
    print("=" * 70)
    print()
    
    # Step 1: Generate login URL
    login_url = get_login_url()
    print("📌 Step 1: Open this URL in your browser and log in:\n")
    print(f"   {login_url}\n")
    print("-" * 70)
    print("📌 Step 2: After login, Kite will redirect to http://127.0.0.1/...")
    print("   The page will NOT load (that's expected).")
    print("   Copy the FULL URL from your browser's address bar.")
    print("-" * 70)
    print()
    
    # Step 2: Get user input
    raw_input = input("📋 Paste the redirect URL or request_token here: ")
    
    try:
        request_token = extract_request_token(raw_input)
        print(f"\n🔑 Extracted request_token: {request_token[:8]}...{request_token[-4:]}")
    except ValueError as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
    
    # Step 3: Exchange for access token
    print("\n⏳ Generating access token...")
    try:
        session = generate_access_token(request_token)
    except RuntimeError as e:
        print(f"\n❌ {e}")
        sys.exit(1)
    
    # Step 4: Save to disk
    save_access_token(session)
    
    print(f"\n👤 Logged in as: {session.get('user_id', 'N/A')}")
    print(f"🕐 Token valid until ~6:00 AM tomorrow.")
    print(f"\n✅ You can now run: python main.py")
    print("=" * 70)
