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

The bot is **long-only** and tracks one position at a time: it buys to open,
then closes on whichever comes first — a stop-loss hit, a take-profit hit, or a
sell signal. Stop-loss / take-profit levels (`stop_loss_percentage`,
`take_profit_percentage`) are enforced by the bot, which checks the current
price against them on every cycle.

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

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

The tests mock the ccxt exchange, so they make no network calls and place no
orders.

## Known limitations

- **Protective exits are bot-side, not exchange-side.** Stop-loss / take-profit
  are enforced by the running bot checking price each cycle (every
  `LOOP_INTERVAL_SECONDS`). If the process stops, or price gaps past a level
  between checks, the exit will be late or missed. Exchange-side bracket/OCO
  orders are not yet implemented.
- **Position state is in-memory only.** Restarting the bot forgets any open
  position, so it will not manage exits for a trade opened in a previous run.
- The backtest is simplified: no fees or slippage; fills at each candle's close.

See `CLAUDE.md` for guidance aimed at AI assistants working in this repo.
