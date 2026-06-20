# Setup guide (Windows, beginner-friendly)

A step-by-step guide to running the bot in **dry-run mode** (real prices, no real
money, places no orders). Take it one step at a time.

> ⚠️ This is real trading software. This guide only sets up the **safe dry run**.
> Do not switch to real money until you have watched the dry run for a while and
> understand the risks. You can lose money when `DRY_RUN=false`.

## 1. Install Python (if you don't have it)

1. Go to <https://www.python.org/downloads/> and click **Download Python**.
2. Run the installer. **Check the box "Add python.exe to PATH"** at the bottom
   before clicking Install. (This makes the `python` command work everywhere.)

## 2. Download the bot

1. Download the project ZIP from GitHub (use the correct branch).
2. Right-click the `.zip` in your Downloads → **Extract All…** → **Extract**.
3. Open the extracted folder until you see `trading_bot.py` directly inside it.

## 3. Open a terminal in that folder

- Right-click inside the folder → **Open in Terminal**, **or** open VS Code,
  **File → Open Folder…**, pick the folder, then **Terminal → New Terminal**.

## 4. Install the libraries

```powershell
python -m pip install -r requirements.txt
```

If Windows says Python "was not found" or `python` isn't recognized, use the
full path to your Python instead, for example:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" -m pip install -r requirements.txt
```

## 5. Create a read-only Coinbase API key

1. Go to <https://www.coinbase.com/settings/api> → **Create API key**.
2. Give it **view / read-only** permission (no trading) for dry-run testing.
3. Copy the **API key** and the **API secret** — Coinbase shows the secret only
   once. Keep them private; never share them or commit them.

## 6. Create your .env file

1. In the project folder, make a copy of `.env.example` and name the copy `.env`.
2. Open `.env` and fill in your values:
   - `COINBASE_API_KEY=` your key id
   - `COINBASE_API_SECRET="..."` your EC private key, on one line, in double
     quotes, with line breaks written as `\n` (see `.env.example` for the exact
     format).
   - Leave `DRY_RUN=true`.
   - Email lines are optional — leave them blank to skip notifications.

`.env` is git-ignored, so your secrets stay on your machine only.

## 7. Run the dry run

```powershell
python trading_bot.py
```

(or the full-path version from step 4, with `trading_bot.py` at the end).

You'll see it backtest once, then loop every 60 seconds: fetching the price,
computing indicators, and logging what it *would* do. Most cycles say
"No action this cycle" — that's normal. Nothing is bought or sold. Activity is
also written to `trading_bot.log`.

To stop the bot, click the terminal and press **Ctrl + C**.

## Going to real money (later, optional)

Only after you've watched the dry run and accept the risk:

1. Create a **new** API key **with trade permission**.
2. Put it in `.env`, fund the account with a small amount you can afford to lose.
3. Change `DRY_RUN=true` to `DRY_RUN=false`.
4. Leave `USE_EXCHANGE_PROTECTIVE_ORDERS=false` until you've verified it with one
   tiny trade (it is unverified against live Coinbase).

See `README.md` and `CLAUDE.md` for more detail.
