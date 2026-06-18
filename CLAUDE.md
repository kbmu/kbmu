# CLAUDE.md

Guidance for AI assistants (and humans) working in this repository.

## Security — read first

Secrets must **never** be hard-coded in source. They are read from environment
variables only (see `.env.example`). `.env`, `*.log`, and key files are
git-ignored.

History note: earlier commits of `README.md` contained a live Coinbase API
key/EC private key and a Gmail password. **Those credentials have since been
revoked/rotated by the owner** and the current working tree no longer contains
them. They do, however, still exist in older git commits. If this repo is ever
made public, scrub history first with `git filter-repo` or BFG. Never reproduce
any secret value in commits, comments, PRs, or chat.

## What this repository is

An **algorithmic cryptocurrency trading bot** written in Python. It trades a
configured pair (default `XRP/USD`) on Coinbase via the
[`ccxt`](https://github.com/ccxt/ccxt) library, using a multi-indicator
technical-analysis strategy with optional email notifications.

The bot defaults to **`DRY_RUN` mode** — it logs the orders it *would* place
without sending them to the exchange. Real trading only happens when
`DRY_RUN=false`.

## Repository structure

```
.
├── trading_bot.py       # The trading bot (single-module Python program)
├── tests/
│   └── test_trading_bot.py  # pytest smoke tests (mock the ccxt exchange)
├── requirements.txt     # Runtime dependencies (ccxt, pandas, numpy, ta)
├── requirements-dev.txt # Runtime deps + pytest
├── pytest.ini           # pytest configuration
├── .env.example         # Template for required environment variables
├── .gitignore           # Ignores .env, logs, key files, Python caches
├── README.md            # User-facing setup & usage docs
└── CLAUDE.md            # This file
```

## How the bot works

The strategy combines indicators (from the [`ta`](https://github.com/bukosabino/ta)
library) and only trades when they agree:

- **RSI** (14) — overbought (70) / oversold (30)
- **SMA** (50) — short-term trend
- **MACD** (12/26/9) — momentum crossover
- **Bollinger Bands** (20, 2σ) — volatility envelope
- **Trend SMA** (200) — longer-term trend confirmation

All tunables live in the `parameters` dict near the top of `trading_bot.py`.

The bot is **long-only** and tracks a single open position via the `Position`
namedtuple (`size`, `entry_price`, `stop_loss`, `take_profit`,
`protective_order_ids`). The decision logic is a small state machine in
`evaluate`: when flat, a buy signal opens a position; when in a position, a
stop-loss or take-profit hit (priority) or a sell signal closes it.

Exits can be enforced two ways:

- **Bot-side (default):** price is checked against the levels each cycle.
- **Exchange-side (opt-in, `USE_EXCHANGE_PROTECTIVE_ORDERS=true`):** a server-side
  bracket/OCO order is placed at entry; `evaluate` is called with
  `exchange_managed_exits=True` so it doesn't double-close on price, and
  `run_once` reconciles a filled bracket back to flat. This path is **unverified
  against live Coinbase** — treat changes to it conservatively.

The position is persisted to `POSITION_STATE_FILE` after each cycle and reloaded
on startup (`save_position` / `load_position`), so exits survive a restart.

Key functions:

| Function | Responsibility |
|----------|----------------|
| `build_exchange` | Construct the ccxt client; errors if credentials are missing |
| `send_email` | Optional SMTP notification; no-op if email env vars are unset |
| `get_balance` / `get_market_price` | Account balance / latest price via ccxt |
| `fetch_historical_data` | OHLCV candles (1m) into a pandas DataFrame |
| `calculate_indicators` | Returns an `Indicators` namedtuple (no positional indexing) |
| `position_size_for` | Size a position from `balance × risk_percentage / price` |
| `should_buy` / `should_sell` | Named strategy conditions |
| `protective_levels` | Compute stop-loss / take-profit prices for a long entry |
| `evaluate` | Pure state machine returning `(action, reason)`; `exchange_managed_exits` skips bot-side SL/TP |
| `place_order` | Submit market orders, or simulate them when `DRY_RUN` is on |
| `place_protective_orders` / `cancel_protective_orders` / `protective_order_filled` | Exchange-side bracket order lifecycle (opt-in) |
| `save_position` / `load_position` | Persist/restore the open `Position` (JSON) |
| `buy_fill_price` / `sell_fill_price` / `trade_fee` | Backtest slippage & fee modelling |
| `backtest_strategy` | Replays the strategy (incl. SL/TP, fees, slippage); returns final balance |
| `run_once` | One live decision cycle; takes/returns the current `Position` |
| `main` | Backtests once, then loops every `LOOP_INTERVAL_SECONDS` |

Execution flow (`main`): validate creds → backtest on startup → loop: fetch
price → fetch candles → compute indicators → `evaluate` action → open/close (or
simulate) → sleep. Loop errors are logged and the loop continues.

Logging goes to `trading_bot.log` and is echoed to the console.

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

Tests mock the ccxt exchange (no network, no real orders). `evaluate`,
`should_buy`/`should_sell`, `position_size_for`, `protective_levels`, and
`place_order` are pure/easily mocked — keep them that way so new logic stays
testable. Tests force `DRY_RUN` via `monkeypatch.setattr(bot, "DRY_RUN", ...)`.

CI runs the suite on every push and PR via `.github/workflows/tests.yml`
(Python 3.11 and 3.12).

## Configuration (environment variables)

Read by `trading_bot.py`; see `.env.example` for the template.

- `COINBASE_API_KEY`, `COINBASE_API_SECRET` — required.
- `TRADING_SYMBOL` (default `XRP/USD`), `LOOP_INTERVAL_SECONDS` (default 60).
- `DRY_RUN` (default `true`) — set `false` only to trade real funds.
- `USE_EXCHANGE_PROTECTIVE_ORDERS` (default `false`) — opt into server-side
  bracket exits (unverified against live Coinbase).
- `POSITION_STATE_FILE` (default `position_state.json`) — where the open position
  is persisted.
- `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `NOTIFY_EMAIL` — optional email alerts
  (use a Gmail App Password, not the account password).

## Development workflow

```bash
pip install -r requirements.txt
cp .env.example .env            # then edit .env with real values
set -a && source .env && set +a # load env vars
python trading_bot.py
```

Quick syntax check: `python -m py_compile trading_bot.py`.

### Git conventions

- Active development branch: **`claude/claude-md-docs-ssv7hd`** (do not push to
  `main` without explicit permission).
- Push with `git push -u origin <branch-name>`.
- Do not open pull requests unless explicitly asked.

## Known issues & conventions for future changes

- **Secrets**: keep them in env vars only; never hard-code or echo them.
- **Exchange-side bracket orders are unverified**: `place_protective_orders`
  uses ccxt unified params that have not been confirmed against live Coinbase.
  Be conservative changing this path; it must be tested with minimal real funds.
  Bot-side monitoring remains the default and the reliable fallback.
- **Bot-side exits run once per cycle**: even with persistence, a stopped process
  or a price gap between checks can delay/miss a bot-side exit.
- **Backtest models fees & slippage** (`fee_rate`, `slippage` parameters) and
  returns the final balance, but still fills at each candle's close and always
  uses bot-side exits. It makes no network calls.
- **Live trading risk**: this code can place **real market orders**. Keep
  `DRY_RUN=true` for any testing; never point it at a funded account casually.

## Guidance for AI assistants

- Never paste, echo, or relocate credentials; use environment-variable lookups.
- Be conservative with anything affecting live trading behavior — explain
  trade-offs and confirm before changing order logic, sizing, or thresholds.
- Match the existing style (single-module, procedural, `logging`-based) for
  small changes; propose larger restructures explicitly.
