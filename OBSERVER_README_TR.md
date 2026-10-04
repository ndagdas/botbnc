# Telegram Sinyal Gözlemcisi

Bu klasör, mevcut Binance işlem botundan ayrı çalışacak gözlem sürümüdür. `app.py`
Binance SDK'sı içermez, Binance'e HTTP isteği yapmaz ve hiçbir emir açamaz. TradingView
webhook'larını değerlendirip yalnızca Telegram'a mesaj gönderir.

## Gerekli ortam değişkenleri

- `WEBHOOK_SECRET`: TradingView alarm mesajındaki `webhookSecret` ile aynı gizli değer.
- `TELEGRAM_BOT_TOKEN`: Telegram bot token'ı.
- `TELEGRAM_CHAT_ID`: Sinyal/analiz mesajlarının gönderileceği sohbet.
- `AI_REVIEW_ENABLED`: Varsayılan `false`. `true` yapılırsa ayrıca `OPENAI_API_KEY` gerekir.
- `OPENAI_MODEL`: Opsiyonel, varsayılan `gpt-5-mini`; AI kapalıyken kullanılmaz.
- `MAX_ENTRY_CANDLE_PCT`: Geç giriş filtresi, varsayılan `4.0` yüzde.

Kimlik bilgilerini TradingView JSON'una eklemeyin. Uygulama TradingView payload'ından
yalnızca teknik alanların izin listesini alır; webhook sırrını doğruladıktan sonra atar.

## Dağıtım

Bu dizin ayrı bir Heroku uygulaması/repository kökü olarak dağıtılmalıdır. Var olan
uygulamaya veya `main` dalına bağlamayın. Procfile yalnızca tek `web` süreci başlatır.
TradingView webhook URL'si `/webhook` (veya `/monitor/webhook`), HTTP yöntemi POST.

Örnek payload (TradingView alarmı kapanmış mumda tetiklenecek şekilde ayarlanmalı):

```json
{
  "webhookSecret": "SERVER_SECRET",
  "signalId": "{{ticker}}-{{interval}}-{{time}}-LONG",
  "ticker": "{{ticker}}",
  "side": "LONG",
  "timeframe": "{{interval}}",
  "price": "{{close}}",
  "volumeRatio": 1.8,
  "rsi": 61,
  "adx": 24,
  "rangeMult": 1.3,
  "breakoutConfirmed": true,
  "entryMovePct": 1.2,
  "riskReward": 1.7,
  "btcTrend": "LONG"
}
```

Eksik indikatörler tahmin edilmez. Gelen sinyallerin tümüne karar ve gerekçeler
Telegram'da bildirilir. Kural puanı ilk deneme için gözlem amaçlıdır; kârlılık garantisi
veya backtest sonucu değildir. AI incelemesi sadece ikinci görüş üretir, kural tabanlı
RED güvenlik eşiklerini AL'a yükseltemez.
