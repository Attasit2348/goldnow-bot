from flask import Flask, render_template, jsonify
import requests
import threading
import time
import pandas as pd
from datetime import datetime

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = "8986056921:AAGRWD4YpmhnoHL47__TmzA6qa5JR4ShDHw"
TELEGRAM_CHAT_ID = "6713234903"

raw_ticks = []
ohlc_candles = []

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

def fetch_hsh_api():
    global raw_ticks, ohlc_candles, latest_price_data
    last_signal = "WAIT"

    while True:
        try:
            res = requests.get("https://apicheckpricev3.huasengheng.com/api/Values/GetPrice", timeout=5)
            if res.status_code == 200:
                data = res.json()
                
                if isinstance(data, list) and len(data) > 0:
                    item = data[0]
                elif isinstance(data, dict):
                    item = data
                else:
                    item = {}

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
                    
                    # ปรับเป็น 1min เพื่อประมวลผลแท่งเทียนได้รวดเร็ว
                    df_ohlc = df_ticks['price'].resample('1min').ohlc().dropna()
                    
                    chart_data = []
                    for idx, row in df_ohlc.iterrows():
                        chart_data.append({
                            "time": int(idx.timestamp()),
                            "open": float(row['open']),
                            "high": float(row['high']),
                            "low": float(row['low']),
                            "close": float(row['close'])
                        })
                    ohlc_candles = chart_data

                    if len(df_ohlc) >= 3:
                        # คำนวณ EMA21, EMA50 และ RSI14 ด้วย pandas ดั้งเดิม
                        df_ohlc['ema21'] = df_ohlc['close'].ewm(span=min(21, len(df_ohlc)), adjust=False).mean()
                        df_ohlc['ema50'] = df_ohlc['close'].ewm(span=min(50, len(df_ohlc)), adjust=False).mean()

                        delta = df_ohlc['close'].diff()
                        gain = delta.where(delta > 0, 0)
                        loss = -delta.where(delta < 0, 0)
                        avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
                        avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
                        rs = avg_gain / (avg_loss + 1e-10)
                        df_ohlc['rsi'] = 100 - (100 / (1 + rs))

                        last_row = df_ohlc.iloc[-1]
                        close_p = last_row['close']
                        ema21_p = last_row.get('ema21', close_p)
                        ema50_p = last_row.get('ema50', close_p)
                        rsi_p = last_row.get('rsi', 50)

                        long_score = 0
                        if close_p >= ema21_p: long_score += 3
                        if ema21_p >= ema50_p: long_score += 3
                        if rsi_p >= 50: long_score += 2
                        if rsi_p < 70: long_score += 2

                        short_score = 0
                        if close_p <= ema21_p: short_score += 3
                        if ema21_p <= ema50_p: short_score += 3
                        if rsi_p <= 50: short_score += 2
                        if rsi_p > 30: short_score += 2

                        current_signal = "WAIT"
                        if long_score >= 8:
                            current_signal = "BUY"
                        elif short_score >= 8:
                            current_signal = "SELL"

                        latest_price_data["signal"] = current_signal
                        latest_price_data["score"] = f"Long: {long_score}/10 | Short: {short_score}/10"

                        if current_signal != last_signal and current_signal != "WAIT":
                            msg = f"🔔 [Gold Now Alert]\nสัญญาณ: {current_signal}\nราคาขายออก: {sell_price:,.0f} บาท\nRSI: {rsi_p:.1f}\nเวลา: {now.strftime('%H:%M:%S')}"
                            send_telegram_alert(msg)
                            last_signal = current_signal
                    else:
                        latest_price_data["score"] = f"รอแท่งเทียน ({len(df_ohlc)}/3)"

        except Exception as e:
            print(f"Error fetching HSH API: {e}")

        time.sleep(5)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/candles")
def get_candles():
    return jsonify(ohlc_candles)

@app.route("/api/live")
def get_live():
    return jsonify(latest_price_data)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    threading.Thread(target=fetch_hsh_api, daemon=True).start()
    app.run(host="0.0.0.0", port=port, debug=False)