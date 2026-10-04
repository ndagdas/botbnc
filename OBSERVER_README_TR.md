# Botreel için Telegram gözlem rotası

Bu ekleme Botreel'in mevcut `/webhook` ve Binance işlem akışını değiştirmez.
TradingView'de yalnızca gözlem için ayrı alarm açıp URL sonuna
`/observer/webhook` ekleyin. Bu rota sinyali değerlendirir ve Telegram'a
`AL / İZLE / RED` ile gerekçeleri yollar; Binance emri göndermez.

## Gerekli Heroku Config Vars

- `WEBHOOK_SECRET` (Botreel'de mevcutsa aynısını kullanır)
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- İsteğe bağlı: `MAX_ENTRY_CANDLE_PCT` (varsayılan 4.0)
- İsteğe bağlı: `AI_REVIEW_ENABLED=true` ve `OPENAI_API_KEY`

Gizli değerleri TradingView alarm JSON'una koymayın. Gözlem alarmı JSON'unda
`webhookSecret`, `ticker`, `side` ve varsa `timeframe`, `price`, `volumeRatio`,
`rsi`, `adx`, `rangeMult`, `breakoutConfirmed`, `entryMovePct`, `riskReward`,
`btcTrend` alanları olabilir. Eksik indikatörler tahmin edilmez. 8% ve üstü mum
hareketi varsayılan olarak geç giriş sayılıp RED olur.

Mevcut `/webhook` adresine bağlı canlı alarmı değiştirmeyin; o adres Botreel'in
mevcut işlem botunu kullanmaya devam eder. İlk gözlem için ayrı TradingView alarmı
oluşturup `/observer/webhook` adresine gönderin.

Gözlem kuyruğu ilk sürümde bellek içindedir; uygulama yeniden başlarsa o sırada
kuyrukta kalan bildirimler kaybolabilir. `/observer/health` yalnızca mod ve
Telegram yapılandırmasının hazır olup olmadığını gösterir.
