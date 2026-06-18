# Crypto Trading Bot

An algorithmic cryptocurrency trading bot that trades a configured pair on
Coinbase via [`ccxt`](https://github.com/ccxt/ccxt), using a multi-indicator
technical-analysis strategy.

> ⚠️ **This software places real orders with real money when not in dry-run
> mode. Trading is risky and you can lose funds. Use at your own risk, and test
> thoroughly in `DRY_RUN` mode first.**

## Strategy

A trade signal requires several indicators to agree:

- **RSI** (14) — overbought (70) / oversold (30)
- **SMA** (50) — short-term trend
- **MACD** (12/26/9) — momentum crossover
- **Bollinger Bands** (20, 2σ) — volatility envelope
- **Trend SMA** (200) — longer-term trend confirmation

**Buy** when oversold, price above the short SMA and long trend, MACD bullish,
and price below the lower Bollinger band. **Sell** is the mirror condition.
All tunables live in the `parameters` dict in `trading_bot.py`.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Configure secrets and settings:
   ```bash
   cp .env.example .env
   # edit .env with your real Coinbase API key/secret
   ```
   Create the Coinbase API key at <https://www.coinbase.com/settings/api>.
   For email alerts, use a Gmail **App Password**, not your account password.

## Running

```bash
set -a && source .env && set +a   # load environment variables
python trading_bot.py
```

The bot runs a quick backtest on startup, then enters a loop that evaluates the
strategy every `LOOP_INTERVAL_SECONDS` (default 60s). Activity is written to
`trading_bot.log` and echoed to the console.

### Dry run (default & recommended)

`DRY_RUN=true` (the default) logs the orders it *would* place without sending
them to the exchange. Only set `DRY_RUN=false` once you have verified behavior
and intend to trade real funds.

## Security notes

- Secrets are read from environment variables only — never hard-code them.
- `.env`, `*.log`, and key files are git-ignored. Keep them out of version
  control.
- If a key is ever exposed, revoke/rotate it immediately at the Coinbase API
  settings page and change any related passwords.

## Known limitations

- `stop_loss_percentage` / `take_profit_percentage` are computed and logged but
  **not yet enforced** as exchange-side protective orders. Do not rely on them
  for risk control.
- The backtest is simplified: no fees, slippage, or stop/take exits, and fills
  at each candle's close.
- There is no automated test suite yet.

See `CLAUDE.md` for guidance aimed at AI assistants working in this repo.
