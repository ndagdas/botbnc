# Botreel observer v2

The existing root /webhook trading route is preserved. Only /observer/webhook is observation-only. Point TradingView alarms to that observer route.

## Configuration

Required: WEBHOOK_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
Optional AI: AI_REVIEW_ENABLED=true, OPENAI_API_KEY, OPENAI_MODEL=gpt-5-mini. Create and enter keys manually; never put them in Pine, alerts or repository files. API usage is separately billed by the provider. AI_MAX_CALLS_PER_DAY defaults to 200 including follow-up reviews and failed attempts. Hard rejects bypass AI. Model failures fall back to clearly labelled rule review, never pretend an AI review occurred.

WATCH_ENABLED=true by default. WATCH_INTERVAL_SECONDS=60 (minimum 30), WATCH_TTL_MINUTES=120, WATCH_NOTIFY_MINUTES=15, MAX_ACTIVE_WATCHES=50, MAX_WATCH_CHASE_PCT=4. These are initial observation settings, not backtested optimal parameters.

## Follow-up

Only WATCH decisions create price tracking. Public Binance USD-M USDT price and closed candles are fetched; no exchange account or order API is used. Original stop or TP1 reached, excessive directional price advance, or expiry terminate the watch. A newly closed candle triggers fresh RSI, ADX, volume, range, EMA and breakout review, with current price versus original stop/TP1 for risk/reward. Polls within the same candle do not call AI again. Telegram uses a retryable outbox (delivery at least once; a crash after successful delivery before acknowledgement can duplicate a message).

## Persistence and operation

Use OBSERVER_DATABASE_URL or DATABASE_URL for PostgreSQL restart persistence. Without it SQLite runs on the dyno filesystem and pending watches/outbox are LOST on Heroku restart or deployment. Health exposes restart_safe=false in that case. Do not describe this fallback as uninterrupted tracking. Requires an always-running web dyno; sleeping/stopped dynos stop background tracking. Configure one Gunicorn worker in the existing Procfile. Webhook acknowledges after storage rather than waiting for AI or Telegram.

/observer/health exposes worker, queue, storage, Telegram and AI configuration plus last API result, without credentials. ai_configured means a key exists, not that it works; a successful review must set ai_state.last_success_at. Test Telegram delivery and Binance public-data access before enabling production alarms.

After editing Pine, recreate TradingView alerts because existing alarms retain their previous script snapshot and inputs. New source is pine/Pump_Early_Observer_v2_3_4_3.pine. Core entry and exit conditions are unchanged. Observer JSON carries no Binance or Telegram credentials.

Run tests from the repository root: python -m unittest observer.test_app -v.
