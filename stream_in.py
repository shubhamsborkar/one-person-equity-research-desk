"""Breeze websocket tick streaming for the India watch (read-only market data).

One socket on the first live account's client, up only during the NSE session
(the exchange feed is silent outside it anyway). Ticks are normalized into the
same schema the REST poller writes into WATCH, so every surface that reads
WATCH gets faster prices with zero further changes. The REST poller keeps
running as the fallback and backs off while ticks are fresh.

The order-notification channel is never opened (get_order_notification stays
False) — this desk places no orders.
"""

import threading
import time
from datetime import datetime

# symbol prefix digit -> (token_script_dict_list index, exchange label)
_EXCH = {"4": (1, "NSE"), "1": (0, "BSE")}

state = {"last_tick": 0.0, "subs": {}, "err": ""}   # subs: code -> exch
_lock = threading.Lock()


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def healthy():
    """True while ticks are actually arriving (fresh within 90s)."""
    with _lock:
        return time.time() - state["last_tick"] < 90


def _make_handler(breeze, sink):
    def on_ticks(tick):
        try:
            if not isinstance(tick, dict):
                return
            sym = tick.get("symbol") or ""
            if "!" not in sym:
                return
            head, token = sym.split("!", 1)
            exch = _EXCH.get(head.split(".")[0])
            if exch is None:
                return
            row = breeze.token_script_dict_list[exch[0]].get(token)
            if not row:
                return
            last = _num(tick.get("last"))
            prev = _num(tick.get("close"))
            if last is None or last <= 0:
                return
            # day % computed here: the tick's own change field loses the sign,
            # same as the REST quote.
            q = {
                "code": row[0], "exch": exch[1], "ltp": last, "prev": prev,
                "day_pct": ((last - prev) / prev * 100) if prev else None,
                "bid": _num(tick.get("bPrice")), "bid_qty": _num(tick.get("bQty")),
                "offer": _num(tick.get("sPrice")), "offer_qty": _num(tick.get("sQty")),
                "open": _num(tick.get("open")), "high": _num(tick.get("high")),
                "low": _num(tick.get("low")), "ttq": _num(tick.get("ttq")),
                "ts": datetime.now().strftime("%H:%M:%S"),
            }
            with _lock:
                state["last_tick"] = time.time()
            sink(row[0], q)
        except Exception:  # noqa: BLE001 — a bad tick must never kill the socket
            pass
    return on_ticks


def manager(breeze, load_names, sink, market_open):
    """Runs forever: socket up in session, down outside; subscriptions re-synced
    against the watchlist every pass so names added in the UI start streaming."""
    connected = False
    while True:
        try:
            if market_open():
                if not connected:
                    breeze.on_ticks = _make_handler(breeze, sink)
                    breeze.ws_connect()
                    connected = True
                    with _lock:
                        state["subs"] = {}
                        state["err"] = ""
                    print("  stream: websocket up")
                want = {e["code"]: e.get("exch", "NSE")
                        for e in load_names()
                        if e.get("source", "breeze") == "breeze"}
                with _lock:
                    have = dict(state["subs"])
                for code in [c for c in want if c not in have]:
                    try:
                        breeze.subscribe_feeds(
                            exchange_code=want[code], stock_code=code,
                            product_type="cash",
                            get_exchange_quotes=True, get_market_depth=False)
                        with _lock:
                            state["subs"][code] = want[code]
                    except Exception:  # noqa: BLE001 — poller still covers it
                        pass
                    time.sleep(0.25)
                for code in [c for c in have if c not in want]:
                    try:
                        breeze.unsubscribe_feeds(
                            exchange_code=have[code], stock_code=code,
                            product_type="cash",
                            get_exchange_quotes=True, get_market_depth=False)
                    except Exception:  # noqa: BLE001
                        pass
                    with _lock:
                        state["subs"].pop(code, None)
            elif connected:
                try:
                    breeze.ws_disconnect()
                except Exception:  # noqa: BLE001
                    pass
                connected = False
                with _lock:
                    state["subs"] = {}
                print("  stream: websocket down (session over)")
        except Exception as exc:  # noqa: BLE001
            with _lock:
                state["err"] = str(exc)[:200]
            try:
                breeze.ws_disconnect()
            except Exception:  # noqa: BLE001
                pass
            connected = False
            time.sleep(30)
        time.sleep(15)
