# CLAUDE.md

Guidance for AI assistants (and humans) working in this repository.

## ⚠️ Security alert — read first

`README.md` currently contains **hardcoded, live-looking secrets** committed in
plaintext:

- A Coinbase Advanced Trade **API key** and **EC private key** (`API_KEY` /
  `API_SECRET`).
- A Gmail account **address and password** used for SMTP notifications
  (`send_email`).

These are exposed in git history and must be treated as **compromised**.
If you are assisting the owner, the priority actions are:

1. **Revoke/rotate** the Coinbase API key and change the Gmail password
   immediately (use a Gmail App Password, not the account password).
2. Move all secrets to environment variables (e.g. `os.environ[...]`) or a
   `.env` file that is **git-ignored**.
3. Scrub the secrets from git history (`git filter-repo` or BFG) before the
   repository is shared or made public.

**Never** reproduce these secret values in commits, comments, PRs, chat, or any
other artifact. When editing the code, remove the literals rather than copying
them.

## What this repository is

A single-file **algorithmic cryptocurrency trading bot** written in Python. It
trades a configured pair (default `XRP/USD`) on Coinbase via the
[`ccxt`](https://github.com/ccxt/ccxt) library, using a multi-indicator
technical-analysis strategy with email notifications.

> Note: the program lives in `README.md`, but its contents are **Python source
> code**, not Markdown documentation. See "Known issues / conventions" below.

## Repository structure

```
.
├── README.md     # The entire trading bot (Python source, ~170 lines)
└── CLAUDE.md     # This file
```

There is currently no `requirements.txt`, no test suite, no package layout, and
no separate `.py` entry point. The whole program is in one file.

## How the bot works

The strategy combines several indicators (from the [`ta`](https://github.com/bukosabino/ta)
library) and only trades when they agree:

- **RSI** (period 14) — overbought (70) / oversold (30) thresholds.
- **SMA** (period 50) — short-term trend.
- **MACD** (12/26/9) — momentum crossover.
- **Bollinger Bands** (window 20, 2 std) — volatility envelope.
- **Trend SMA** (window 200) — longer-term trend confirmation.

All tunable values live in the `parameters` dict near the top of the file
(periods, thresholds, `risk_percentage`, `stop_loss_percentage`,
`take_profit_percentage`, `trend_window`).

Key functions:

| Function | Responsibility |
|----------|----------------|
| `send_email` | SMTP notification on trades/errors |
| `get_balance` | Fetch account USD balance via ccxt |
| `get_market_price` | Latest ticker price |
| `fetch_historical_data` | OHLCV candles (1m timeframe) into a pandas DataFrame |
| `calculate_indicators` | Compute the indicator tuple used by the strategy |
| `calculate_position_size` | Size a position from balance × risk_percentage |
| `place_order` | Submit market buy/sell orders, notify by email |
| `set_stop_loss` / `set_take_profit` | Currently **log only** — they do not place real protective orders |
| `backtest_strategy` | Replays historical candles against the strategy |
| `main` | Backtests once, then runs an infinite live loop (60s interval) |

Execution flow (`main`): backtest on startup → then loop forever: fetch price →
fetch candles → compute indicators → check buy/sell conditions → place order →
sleep 60s. Errors are caught and logged, then the loop retries.

Logging goes to `trading_bot.log` (configured via `logging.basicConfig`).

## Development workflow

There is no build system. To run or modify the bot:

1. **Treat `README.md` as Python.** Either copy it to a `.py` file or rename it
   (recommended — see below) before running.
2. Install dependencies (no manifest exists yet; these are the imports used):
   ```bash
   pip install ccxt pandas numpy ta
   ```
   (`os`, `time`, `logging`, `smtplib`, `email` are standard library.)
3. Provide credentials via environment variables (after the security fix), then:
   ```bash
   python trading_bot.py
   ```

### Git conventions for this repo

- Active development branch: **`claude/claude-md-docs-ssv7hd`** (do not push to
  `main` without explicit permission).
- Push with `git push -u origin <branch-name>`.
- Do not open pull requests unless explicitly asked.

## Known issues & recommended conventions

When asked to improve this codebase, keep these in mind:

- **Secrets**: highest priority — see the security alert above.
- **Misnamed file**: the code should live in something like `trading_bot.py`,
  with a real `README.md` for documentation. Preserve this only if the owner
  has an external reason for the current layout.
- **No dependency manifest**: add a `requirements.txt` (or `pyproject.toml`)
  pinning `ccxt`, `pandas`, `numpy`, `ta`.
- **Stop-loss / take-profit are not enforced**: `set_stop_loss` and
  `set_take_profit` only write log lines; no protective orders are placed on the
  exchange. Flag this before relying on them.
- **Backtest realism**: `backtest_strategy` calls `get_market_price()` (a live
  network call) and `calculate_position_size()` during replay, mixing live data
  into historical simulation. This is a correctness bug to surface, not silently
  "fix," since it changes behavior.
- **No tests**: there is no automated testing. New logic should ideally come
  with at least a smoke test that mocks the `ccxt` exchange.
- **Live trading risk**: this code places **real market orders**. Never run it
  against a funded account for testing — use ccxt sandbox/paper mode or mocked
  responses.

## Guidance for AI assistants

- Do not paste, echo, or relocate the embedded credentials. If you must edit
  lines containing them, replace the literals with environment-variable lookups.
- Be conservative with anything that affects live trading behavior — explain
  trade-offs and ask before changing order logic, sizing, or thresholds.
- Match the existing code style (procedural, single-module, `logging`-based) when
  making small changes; propose a restructure explicitly rather than doing it
  implicitly.
