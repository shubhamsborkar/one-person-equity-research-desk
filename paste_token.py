"""Save today's broker session token for the shipped ICICI Direct (Breeze) adapter.

Only that adapter needs this: its regulator requires a fresh login every trading day.
Most other brokers keep an API session alive for weeks or months. Run by the
"Paste Token" double-click files; nothing to type by hand.
"""
import webbrowser

from breeze_session import ACCOUNTS, _creds, cache_token

print("")
for account in ACCOUNTS:
    api_key, _ = _creds(account)
    if not api_key or api_key.startswith(("your_", "paste_")):
        continue
    url = f"https://api.icicidirect.com/apiuser/login?api_key={api_key}"
    print(f"Opening the broker login page for the '{account}' account in your browser.")
    webbrowser.open(url)
    print("Log in there. When the page jumps to a 'localhost' address, copy the number after apisession= .")
    token = input(f"Paste the '{account}' token here and press Enter (or just Enter to skip): ").strip()
    if token:
        cache_token(account, token)
        print("  saved.\n")
    else:
        print("  skipped.\n")
