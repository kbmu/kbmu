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

## Exchange-side protective orders (optional, unverified)

By default, stop-loss / take-profit are enforced **bot-side**: the running bot
checks the current price against the levels every `LOOP_INTERVAL_SECONDS` and
exits when one is hit. This is reliable while the bot is running, but if the
process stops or price gaps past a level between checks, the exit is late or
missed.

Setting `USE_EXCHANGE_PROTECTIVE_ORDERS=true` makes the bot place a **server-side
bracket order** (a take-profit limit with an attached stop-loss trigger, OCO) at
entry, so the exchange enforces exits even if the bot is offline. The bot then
reconciles each cycle: if the bracket has filled, it resets to flat; a
discretionary sell signal cancels the bracket and market-sells.

> ⚠️ **This path places real OCO/bracket orders and has NOT been verified against
> live Coinbase from this project.** The exact ccxt/Coinbase order parameters can
> vary by account and API version. Before trusting it: enable it with a **very
> small** amount, place one trade, and confirm in the Coinbase UI that the
> bracket appears and behaves as expected. Until then, leave it `false` and rely
> on bot-side monitoring.

## Position persistence

The open position is written to `POSITION_STATE_FILE` (default
`position_state.json`, git-ignored) after each cycle and reloaded on startup, so
the bot resumes managing exits for a trade opened in a previous run.

## Known limitations

- Bot-side exits only act once per `LOOP_INTERVAL_SECONDS` and only while the bot
  runs; see the exchange-side option above for stronger guarantees.
- Exchange-side bracket orders are **unverified** against live Coinbase — test
  before relying on them.
- The backtest is simplified: no fees or slippage; fills at each candle's close,
  and it always uses bot-side exits.

See `CLAUDE.md` for guidance aimed at AI assistants working in this repo.
