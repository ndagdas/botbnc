import os
import json
from flask import Flask, request, jsonify
from binance.client import Client
from dotenv import load_dotenv

# .env dosyasından API bilgilerini yükle
load_dotenv()

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

# Binance API Bağlantısı
client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

app = Flask(__name__)

@app.route('/')
def home():
    return "TradingView Webhook to Binance Futures is running."

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        print("Received data:", data)

        symbol = data.get("symbol", "")
        action = data.get("action", "").upper()  # BUY, SELL veya STOP
        quantity = float(data.get("quantity", 0.01))
        order_type = data.get("order_type", "MARKET").upper()  # MARKET veya LIMIT
        price = float(data.get("price", 0)) if "price" in data else None
        leverage = int(data.get("leverage", 20))  # Varsayılan kaldıraç: 20

        # Binance Futures Kaldıraç Ayarı
        try:
            client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except Exception as e:
            return jsonify({"error": f"Kaldıraç ayarlanırken hata oluştu: {str(e)}"}), 500

        if action in ["BUY", "SELL"]:
            # Yeni pozisyon açma
            if order_type == "MARKET":
                order = client.futures_create_order(
                    symbol=symbol,
                    side=action,
                    type="MARKET",
                    quantity=quantity
                )
            elif order_type == "LIMIT" and price:
                order = client.futures_create_order(
                    symbol=symbol,
                    side=action,
                    type="LIMIT",
                    quantity=quantity,
                    price=price,
                    timeInForce="GTC"
                )
            else:
                return jsonify({"error": "Invalid order type"}), 400

            return jsonify({"status": "Order placed", "order": order}), 200

        elif action == "STOP":
            # Açık pozisyonları kapatma
            positions = client.futures_position_information()
            for pos in positions:
                if pos["symbol"] == symbol and float(pos["positionAmt"]) != 0:
                    side = "SELL" if float(pos["positionAmt"]) > 0 else "BUY"
                    close_order = client.futures_create_order(
                        symbol=symbol,
                        side=side,
                        type="MARKET",
                        quantity=abs(float(pos["positionAmt"]))
                    )
                    return jsonify({"status": "Position closed", "order": close_order}), 200
            
            return jsonify({"status": "No open positions found"}), 200

        else:
            return jsonify({"error": "Invalid action"}), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
