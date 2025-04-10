import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode
from datetime import datetime, timedelta

# ✅ CONFIG - Replace with your details
API_KEY = "s9quwQkHDljeEWLJYeexOLSL75XKNqBz9NRA5DPVY7ZDvQZ5RmCqEqJS3nVNlsMf"
API_SECRET = "nkyGANNffDD8ZmvcZCvJOx3nSgP1MkrqV5dyCORgKiyXZUdYxGLyF2GqjEch5fNl"
TELEGRAM_BOT_TOKEN = "7565965042:AAHCSXYKkB1roWKbrCpNYO2DJ2sGZJdGgC0"
TELEGRAM_CHAT_ID = "-4684992430"

import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode
from datetime import datetime, timedelta

# ✅ CONFIG - Replace with your actual values
# API_KEY = "YOUR_BINANCE_API_KEY"
# API_SECRET = "YOUR_BINANCE_SECRET"
# TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
# TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"  # Include '-' for group chat

BINANCE_BASE_URL = "https://fapi.binance.com"

# Telegram alert sender
def send_telegram_alert(title, message):
    try:
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": f"{title}\n{message}",
            "parse_mode": "HTML"
        }
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=data,
            timeout=10
        )
        if response.status_code != 200:
            print("❗ Telegram response error:", response.text)
        else:
            print("🔔 Alert sent:", title)
    except Exception as e:
        print("❗ Telegram request error:", e)

# Binance signed request
def signed_request(endpoint, params=None):
    if not params:
        params = {}
    params['timestamp'] = int(time.time() * 1000)
    query_string = urlencode(params)
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    headers = {'X-MBX-APIKEY': API_KEY}
    url = f"{BINANCE_BASE_URL}{endpoint}?{query_string}&signature={signature}"
    return requests.get(url, headers=headers).json()

# Main state tracker
open_positions = {}

while True:
    try:
        positions = signed_request("/fapi/v2/positionRisk")

        if isinstance(positions, list):
            current_keys = set()

            for pos in positions:
                symbol = pos['symbol']
                entry_price = float(pos['entryPrice'])
                position_amt = float(pos['positionAmt'])

                if abs(position_amt) < 1e-8 or entry_price == 0.0:
                    continue

                side = "LONG" if position_amt > 0 else "SHORT"
                key = (symbol, side)
                current_keys.add(key)

                if key not in open_positions:
                    open_positions[key] = {
                        'entry_time': datetime.now(),
                        'sl_set': False,
                        'last_alert': None,
                        'last_sl_price': None,
                        'entry_price': entry_price,
                        'entry_amt': position_amt
                    }
                    send_telegram_alert(
                        f"🚀 New trade opened: {symbol}",
                        f"Entry Price: {entry_price}, Size: {position_amt}, Side: {side}"
                    )

                pos_state = open_positions[key]
                time_open = datetime.now() - pos_state['entry_time']

                orders = signed_request("/fapi/v1/openOrders", {"symbol": symbol})
                if isinstance(orders, list):
                    sl_orders = [o for o in orders if o.get('type') in ["STOP_MARKET", "STOP"]]

                    if not sl_orders:
                        if pos_state['sl_set']:
                            send_telegram_alert(
                                f"❌ SL REMOVED: {symbol}",
                                f"SL removed for position opened at {pos_state['entry_time'].strftime('%H:%M:%S')}"
                            )
                            pos_state['sl_set'] = False
                            pos_state['last_sl_price'] = None
                        elif time_open > timedelta(minutes=5):
                            now = datetime.now()
                            if not pos_state['last_alert'] or (now - pos_state['last_alert']) > timedelta(hours=1):
                                send_telegram_alert(
                                    f"⚠️ SL MISSING: {symbol}",
                                    f"No SL found after 5 mins. Entry: {pos_state['entry_time'].strftime('%H:%M:%S')}"
                                )
                                pos_state['last_alert'] = now
                    else:
                        sl_order = sl_orders[0]
                        sl_price = float(sl_order.get('stopPrice', sl_order.get('price')))
                        if not pos_state['sl_set']:
                            pos_state['sl_set'] = True
                            abs_pct = abs(entry_price - sl_price) / entry_price * 100
                            send_telegram_alert(
                                f"✅ SL SET {'ON TIME' if time_open <= timedelta(minutes=5) else 'LATE'}: {symbol}",
                                f"SL set at {sl_price}. Distance: {abs_pct:.2f}%"
                            )
                        if pos_state['last_sl_price'] and abs(sl_price - pos_state['last_sl_price']) > 1e-6:
                            prev_pct = abs(entry_price - pos_state['last_sl_price']) / entry_price * 100
                            new_pct = abs(entry_price - sl_price) / entry_price * 100
                            send_telegram_alert(
                                f"🔁 SL UPDATED: {symbol}",
                                f"Previous: {prev_pct:.2f}%, New: {new_pct:.2f}%"
                            )
                        pos_state['last_sl_price'] = sl_price

            # Detect closed trades
            keys_to_delete = []
            for key, pos_state in open_positions.items():
                if key not in current_keys:
                    symbol, side = key
                    entry = pos_state['entry_price']

                    # Fetch most recent closed trade for that symbol
                    trades = signed_request("/fapi/v1/userTrades", {"symbol": symbol, "limit": 10})
                    if isinstance(trades, list):
                        trades = sorted(trades, key=lambda x: x['time'], reverse=True)
                        for t in trades:
                            pnl = float(t['realizedPnl'])
                            qty = float(t['qty'])
                            price = float(t['price'])
                            if abs(pnl) > 0:
                                pnl_pct = ((price - entry) / entry * 100) if side == "LONG" else ((entry - price) / entry * 100)
                                result = "Profit" if pnl > 0 else "Loss"
                                send_telegram_alert(
                                    f"📤 Trade exited: {symbol}",
                                    f"{result} of {pnl_pct:.2f}% (${abs(pnl):.2f}) on {side} position.\nExit Price: {price:.2f}"
                                )
                                break

                    keys_to_delete.append(key)

            for key in keys_to_delete:
                del open_positions[key]

    except Exception as e:
        print("❗ Error in main loop:", e)


    time.sleep(60)
