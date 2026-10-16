from flask import Flask, render_template, jsonify, request
import requests
import threading
import time
import pandas as pd
from datetime import datetime

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = "8986056921:AAGRWD4YpmhnoHL47__TmzA6qa5JR4ShDHw"
TELEGRAM_CHAT_ID = "6713234903"

raw_ticks = []
ohlc_candles = {
    "1h": [],
    "4h": [],
    "1d": []
}

latest_price_data = {
    "sell_price": 0,
    "buy_price": 0,
    "last_updated": "-",
    "signal": "WAIT",
    "score": "กำลังสะสมข้อมูล..."
}

def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Error sending alert: {e}")

def parse_price(val):
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    clean_str = str(val).replace(',', '').strip()
    return float(clean_str) if clean_str else 0.0

def calc_indicators(df_ohlc):
    """ฟังก์ชันคำนวณ EMA21, EMA50, EMA200 และ RSI14"""
    if len(df_ohlc) < 3:
        return df_ohlc
    
    # คำนวณ EMA 21, 50, 200
    df_ohlc['ema21'] = df_ohlc['close'].ewm(span=min(21, len(df_ohlc)), adjust=False).mean()
    df_ohlc['ema50'] = df_ohlc['close'].ewm(span=min(50, len(df_ohlc)), adjust=False).mean()
    df_ohlc['ema200'] = df_ohlc['close'].ewm(span=min(200, len(df_ohlc)), adjust=False).mean()

    # คำนวณ RSI 14
    delta = df_ohlc['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    df_ohlc['rsi'] = 100 - (100 / (1 + rs))
    return df_ohlc

def to_chart_data(df_ohlc):
    chart_data = []
    for idx, row in df_ohlc.iterrows():
        item = {
            "time": int(idx.timestamp()),
            "open": float(row['open']),
            "high": float(row['high']),
            "low": float(row['low']),
            "close": float(row['close']),
            "ema21": float(row['ema21']) if 'ema21' in row and not pd.isna(row['ema21']) else None,
            "ema50": float(row['ema50']) if 'ema50' in row and not pd.isna(row['ema50']) else None,
            "ema200": float(row['ema200']) if 'ema200' in row and not pd.isna(row['ema200']) else None
        }
        chart_data.append(item)
    return chart_data

def get_tf_bias(df_ohlc):
    if len(df_ohlc) < 3:
        return 0
    last = df_ohlc.iloc[-1]
    c, e21, e50, rsi = last['close'], last.get('ema21', 0), last.get('ema50', 0), last.get('rsi', 50)
    
    if c >= e21 and e21 >= e50 and rsi >= 50:
        return 1
    elif c <= e21 and e21 <= e50 and rsi <= 50:
        return -1
    return 0

def fetch_hsh_api():
    global raw_ticks, ohlc_candles, latest_price_data
    last_signal = "WAIT"

    while True:
        try:
            res = requests.get("https://apicheckpricev3.huasengheng.com/api/Values/GetPrice", timeout=5)
            if res.status_code == 200:
                data = res.json()
                item = data[0] if isinstance(data, list) and len(data) > 0 else (data if isinstance(data, dict) else {})

                sell_raw = item.get("Sell965") or item.get("Sell") or item.get("sell") or item.get("sellPrice") or 0
                buy_raw = item.get("Buy965") or item.get("Buy") or item.get("buy") or item.get("buyPrice") or 0
                
                sell_price = parse_price(sell_raw)
                buy_price = parse_price(buy_raw)
                
                now = datetime.now()
                now_ts = int(now.timestamp())

                latest_price_data["sell_price"] = sell_price
                latest_price_data["buy_price"] = buy_price
                latest_price_data["last_updated"] = now.strftime("%H:%M:%S")

                if sell_price > 0:
                    raw_ticks.append({"time": now_ts, "price": sell_price})
                    
                    df_ticks = pd.DataFrame(raw_ticks)
                    df_ticks['datetime'] = pd.to_datetime(df_ticks['time'], unit='s')
                    df_ticks.set_index('datetime', inplace=True)
                    
                    df_1h = calc_indicators(df_ticks['price'].resample('1h').ohlc().dropna())
                    df_4h = calc_indicators(df_ticks['price'].resample('4h').ohlc().dropna())
                    df_1d = calc_indicators(df_ticks['price'].resample('1D').ohlc().dropna())
                    
                    ohlc_candles["1h"] = to_chart_data(df_1h)
                    ohlc_candles["4h"] = to_chart_data(df_4h)
                    ohlc_candles["1d"] = to_chart_data(df_1d)

                    b_1h = get_tf_bias(df_1h)
                    b_4h = get_tf_bias(df_4h)
                    b_1d = get_tf_bias(df_1d)

                    current_signal = "WAIT"
                    if b_1h == 1 and b_4h == 1 and b_1d == 1:
                        current_signal = "BUY"
                    elif b_1h == -1 and b_4h == -1 and b_1d == -1:
                        current_signal = "SELL"

                    latest_price_data["signal"] = current_signal
                    
                    def fmt_bias(b): return "BULL" if b == 1 else ("BEAR" if b == -1 else "MIXED")
                    latest_price_data["score"] = f"1h: {fmt_bias(b_1h)} | 4h: {fmt_bias(b_4h)} | 1d: {fmt_bias(b_1d)}"

                    if current_signal != last_signal and current_signal != "WAIT":
                        rsi_val = df_1h.iloc[-1].get('rsi', 0) if len(df_1h) > 0 else 0
                        msg = f"🔔 [Gold Now Alert]\nสัญญาณ: {current_signal} (สอดคล้อง 1H, 4H, 1D)\nราคาขายออก: {sell_price:,.0f} บาท\nRSI 1h: {rsi_val:.1f}\nเวลา: {now.strftime('%H:%M:%S')}"
                        send_telegram_alert(msg)
                        last_signal = current_signal

        except Exception as e:
            print(f"Error fetching HSH API: {e}")

        time.sleep(5)

threading.Thread(target=fetch_hsh_api, daemon=True).start()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/candles")
def get_candles():
    tf = request.args.get('tf', '1h')
    return jsonify(ohlc_candles.get(tf, ohlc_candles["1h"]))

@app.route("/api/live")
def get_live():
    return jsonify(latest_price_data)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)