"""Authenticated Breeze clients, one per family account.

Each account has its own Breeze app (key + secret in .env) and its own DAILY
session token, pasted into the terminal when asked and cached for the day in
session_token_<account>.txt (gitignored). READ-ONLY use; this project never
places orders.
"""

import os
import sys
from datetime import datetime

# python.org's Mac build ships without a wired-up CA bundle, so HTTPS to the
# broker fails with "self-signed certificate in certificate chain". Point
# Python at certifi's bundle BEFORE breeze_connect is imported (it fetches the
# security master at import time). Respect an existing override if set.
try:
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except ImportError:
    pass

from dotenv import load_dotenv

try:
    from breeze_connect import BreezeConnect
except ImportError:
    sys.exit(
        "breeze-connect is not installed.\n"
        "Run:  pip install -r requirements.txt"
    )

# account id -> (key env var, secret env var). One account out of the box.
# To watch a second account (a second login at the same broker), add a line
# such as  "second": ("ACCOUNT2_API_KEY", "ACCOUNT2_API_SECRET")  and put that
# pair in .env. The first account listed is the one whose daily token is
# required; every other account is optional (see get_client_if_cached).
ACCOUNTS = {
    "primary": ("BREEZE_API_KEY", "BREEZE_API_SECRET"),
}

_HERE = os.path.dirname(__file__)


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _cache_path(account):
    return os.path.join(_HERE, f"session_token_{account}.txt")


def _read_cached_token(account):
    path = _cache_path(account)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as fh:
            date_line = fh.readline().strip()
            token = fh.readline().strip()
    except OSError:
        return None
    return token if (date_line == _today() and token) else None


def _write_cached_token(account, token):
    try:
        with open(_cache_path(account), "w") as fh:
            fh.write(f"{_today()}\n{token}\n")
    except OSError:
        pass  # caching is a convenience, never fatal


def _prompt_token(account, api_key):
    label = account
    print(f"\nDaily ICICI session token needed for the '{label}' account (expires at midnight).")
    print("1. Open this in a browser and log in to that account:")
    print(f"   https://api.icicidirect.com/apiuser/login?api_key={api_key}")
    print("2. When it jumps to a 'localhost' page, copy the number after")
    print("   'apisession=' in the address bar.\n")
    return input(f"Paste the '{label}' session token here and press Enter: ").strip()


def _creds(account):
    key_env, secret_env = ACCOUNTS[account]
    api_key = os.getenv(key_env, "").strip()
    api_secret = os.getenv(secret_env, "").strip()
    return api_key, api_secret


def get_client_if_cached(account):
    """Session from today's cached token ONLY — never prompts.

    Returns None when there is no valid token for today or the broker rejects
    it. This is what makes an account OPTIONAL: market data (quotes, candles)
    is not account-scoped, so any one live session can price every account's
    names; only that account's OWN holdings/positions/funds/margin need its
    own token.
    """
    if account not in ACCOUNTS:
        return None
    load_dotenv()
    api_key, api_secret = _creds(account)
    if not api_key or not api_secret or api_key.startswith(("your_", "paste_")):
        return None
    token = _read_cached_token(account)
    if not token:
        return None
    breeze = BreezeConnect(api_key=api_key)
    try:
        breeze.generate_session(api_secret=api_secret, session_token=token)
        return breeze
    except Exception:  # noqa: BLE001
        return None


def cache_token(account, token):
    """Store a session token for today (e.g. pasted from the login redirect
    URL's apisession= value) so every command reuses it without prompting."""
    _write_cached_token(account, token)


def get_client(account="primary"):
    """Return a session-ready BreezeConnect for one account.

    Token resolution: today's cached token, else prompt in the terminal. If the
    token is rejected, clear and prompt once more.
    """
    if account not in ACCOUNTS:
        sys.exit(f"Unknown account '{account}'. Known: {', '.join(ACCOUNTS)}")

    load_dotenv()
    api_key, api_secret = _creds(account)
    if not api_key or not api_secret or api_key.startswith(("your_", "paste_")):
        sys.exit(
            f"{account}'s API key/secret are missing in .env "
            f"({ACCOUNTS[account][0]} / {ACCOUNTS[account][1]}).\n"
            "Set them once from https://api.icicidirect.com/apiuser/home"
        )

    token = _read_cached_token(account) or _prompt_token(account, api_key)

    for attempt in range(2):
        breeze = BreezeConnect(api_key=api_key)
        try:
            breeze.generate_session(api_secret=api_secret, session_token=token)
            _write_cached_token(account, token)
            return breeze
        except Exception as exc:  # noqa: BLE001
            if attempt == 0:
                print(f"\n{account}'s token was rejected ({exc}).")
                print("It may have expired or been mistyped. Let's try again.")
                token = _prompt_token(account, api_key)
            else:
                sys.exit(f"Could not start a Breeze session for {account}. Stopping.")
