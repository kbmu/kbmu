"""Algorithmic cryptocurrency trading bot.

Trades a configured pair on Coinbase via ccxt using a multi-indicator
technical-analysis strategy (RSI, SMA, MACD, Bollinger Bands + trend SMA).

Credentials and settings are read from environment variables (see .env.example).
By default the bot runs in DRY_RUN mode and does NOT place real orders.
"""

import os
import time
import logging
import smtplib
from collections import namedtuple
from email.mime.text import MIMEText

import ccxt
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, MACD
from ta.volatility import BollingerBands

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

logging.basicConfig(
    filename="trading_bot.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
# Also echo logs to the console so it's clear what the bot is doing.
logging.getLogger().addHandler(logging.StreamHandler())

# Credentials come from the environment. NEVER hard-code secrets in source.
API_KEY = os.environ.get("COINBASE_API_KEY")
API_SECRET = os.environ.get("COINBASE_API_SECRET")

# Email notification settings (optional). If unset, email is skipped.
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", GMAIL_ADDRESS)

# Safety switch: when truthy (the default), the bot logs intended trades but
# does NOT place real orders. Set DRY_RUN=false ONLY when you intend to trade
# real funds.
DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() not in ("false", "0", "no")

SYMBOL = os.environ.get("TRADING_SYMBOL", "XRP/USD")
LOOP_INTERVAL_SECONDS = int(os.environ.get("LOOP_INTERVAL_SECONDS", "60"))

parameters = {
    "rsi_period": 14,
    "sma_period": 50,
    "macd_short": 12,
    "macd_long": 26,
    "macd_signal": 9,
    "bollinger_window": 20,
    "bollinger_std": 2,
    "overbought": 70,
    "oversold": 30,
    "risk_percentage": 0.01,
    "stop_loss_percentage": 0.02,
    "take_profit_percentage": 0.05,
    "trend_window": 200,  # Longer-term trend confirmation
}

# Named indicator bundle so callers don't rely on positional tuple indexes.
Indicators = namedtuple(
    "Indicators",
    ["rsi", "sma", "macd", "macd_signal", "bb_upper", "bb_lower", "trend"],
)


def build_exchange():
    """Create the ccxt exchange client, validating credentials are present."""
    if not API_KEY or not API_SECRET:
        raise RuntimeError(
            "Missing credentials. Set COINBASE_API_KEY and COINBASE_API_SECRET "
            "in the environment (see .env.example)."
        )
    return ccxt.coinbase({"apiKey": API_KEY, "secret": API_SECRET})


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #

def send_email(subject, message):
    """Send an email notification. No-op if email is not configured."""
    if not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD and NOTIFY_EMAIL):
        logging.debug("Email not configured; skipping notification: %s", subject)
        return

    msg = MIMEText(message)
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = NOTIFY_EMAIL

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        logging.info("Email sent successfully.")
    except Exception as exc:  # noqa: BLE001 - notification must never crash the bot
        logging.error("Failed to send email: %s", exc)


# --------------------------------------------------------------------------- #
# Market data
# --------------------------------------------------------------------------- #

def get_balance(exchange):
    """Return the available USD balance."""
    balance = exchange.fetch_balance()
    return balance["total"].get("USD", 0.0)


def get_market_price(exchange):
    """Return the latest traded price for the configured symbol."""
    ticker = exchange.fetch_ticker(SYMBOL)
    return ticker["last"]


def fetch_historical_data(exchange):
    """Fetch OHLCV candles as a DataFrame, enough for the longest indicator."""
    limit = (
        max(
            parameters["trend_window"],
            parameters["rsi_period"],
            parameters["sma_period"],
            parameters["macd_long"],
            parameters["bollinger_window"],
        )
        + 1
    )
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe="1m", limit=limit)
    return pd.DataFrame(
        ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )


def calculate_indicators(data):
    """Compute the indicator bundle from a price DataFrame."""
    close = data["close"]
    macd = MACD(
        close,
        window_slow=parameters["macd_long"],
        window_fast=parameters["macd_short"],
        window_sign=parameters["macd_signal"],
    )
    bollinger = BollingerBands(
        close,
        window=parameters["bollinger_window"],
        window_dev=parameters["bollinger_std"],
    )
    return Indicators(
        rsi=RSIIndicator(close, window=parameters["rsi_period"]).rsi().iloc[-1],
        sma=SMAIndicator(close, window=parameters["sma_period"]).sma_indicator().iloc[-1],
        macd=macd.macd().iloc[-1],
        macd_signal=macd.macd_signal().iloc[-1],
        bb_upper=bollinger.bollinger_hband().iloc[-1],
        bb_lower=bollinger.bollinger_lband().iloc[-1],
        trend=SMAIndicator(close, window=parameters["trend_window"]).sma_indicator().iloc[-1],
    )


# --------------------------------------------------------------------------- #
# Strategy
# --------------------------------------------------------------------------- #

def position_size_for(balance, price):
    """Position size in base units given account balance and price."""
    if price <= 0:
        return 0.0
    risk_amount = balance * parameters["risk_percentage"]
    return risk_amount / price


def should_buy(ind, price):
    """Buy when oversold, in an uptrend, with bullish momentum near the lower band."""
    return (
        ind.rsi < parameters["oversold"]
        and price > ind.sma
        and ind.macd > ind.macd_signal
        and price < ind.bb_lower
        and price > ind.trend
    )


def should_sell(ind, price):
    """Sell when overbought, in a downtrend, with bearish momentum near the upper band."""
    return (
        ind.rsi > parameters["overbought"]
        and price < ind.sma
        and ind.macd < ind.macd_signal
        and price > ind.bb_upper
        and price < ind.trend
    )


def place_order(exchange, order_type, amount):
    """Submit a market order (or simulate it in DRY_RUN mode)."""
    if amount <= 0:
        logging.warning("Skipping %s order with non-positive amount %s", order_type, amount)
        return None

    if DRY_RUN:
        logging.info("[DRY_RUN] Would place %s order for %s %s", order_type, amount, SYMBOL)
        return {"dry_run": True, "side": order_type, "amount": amount}

    try:
        if order_type == "buy":
            order = exchange.create_market_buy_order(SYMBOL, amount)
        elif order_type == "sell":
            order = exchange.create_market_sell_order(SYMBOL, amount)
        else:
            raise ValueError(f"Unknown order type: {order_type}")
        logging.info("%s order placed: %s", order_type.capitalize(), order)
        send_email("Trade Executed", f"{order_type} order placed: {order}")
        return order
    except Exception as exc:  # noqa: BLE001
        logging.error("Error placing order: %s", exc)
        send_email("Order Error", f"Error placing order: {exc}")
        return None


def backtest_strategy(data):
    """Replay the strategy over historical candles using only historical data.

    Note: this is a simplified backtest. It does not model fees, slippage, or
    stop-loss/take-profit exits, and uses each candle's close as the fill price.
    """
    initial_balance = 1000.0
    balance = initial_balance
    position = 0.0
    market_price = None

    # Start once enough candles exist for the longest-window indicator.
    warmup = parameters["trend_window"]
    for index in range(warmup, len(data)):
        window = data.iloc[: index + 1]
        ind = calculate_indicators(window)
        market_price = window["close"].iloc[-1]

        if position == 0 and should_buy(ind, market_price):
            size = position_size_for(balance, market_price)
            if size > 0:
                position += size
                balance -= size * market_price
                logging.info("Backtest Buy: %s %s at %s", size, SYMBOL, market_price)

        elif position > 0 and should_sell(ind, market_price):
            balance += position * market_price
            logging.info("Backtest Sell: %s %s at %s", position, SYMBOL, market_price)
            position = 0.0

    if market_price is None:
        logging.warning("Backtest skipped: not enough historical data for warmup.")
        return

    final_balance = balance + position * market_price
    logging.info(
        "Backtest completed. Initial Balance: %s, Final Balance: %s",
        initial_balance,
        final_balance,
    )


def run_once(exchange):
    """Execute a single decision cycle."""
    market_price = get_market_price(exchange)
    logging.info("Current market price: %s", market_price)

    historical_data = fetch_historical_data(exchange)
    ind = calculate_indicators(historical_data)
    logging.info("Current indicators: %s", ind)

    balance = get_balance(exchange)
    size = position_size_for(balance, market_price)

    if should_buy(ind, market_price):
        logging.info("Buy conditions met.")
        place_order(exchange, "buy", size)
        logging.info(
            "Stop loss target: %s, take profit target: %s",
            market_price * (1 - parameters["stop_loss_percentage"]),
            market_price * (1 + parameters["take_profit_percentage"]),
        )
    elif should_sell(ind, market_price):
        logging.info("Sell conditions met.")
        place_order(exchange, "sell", size)
        logging.info(
            "Stop loss target: %s, take profit target: %s",
            market_price * (1 + parameters["stop_loss_percentage"]),
            market_price * (1 - parameters["take_profit_percentage"]),
        )
    else:
        logging.info("No trade signal this cycle.")


def main():
    if DRY_RUN:
        logging.info("Starting in DRY_RUN mode: no real orders will be placed.")
    else:
        logging.warning("DRY_RUN is OFF: real orders WILL be placed with real funds.")

    exchange = build_exchange()

    historical_data = fetch_historical_data(exchange)
    backtest_strategy(historical_data)

    while True:
        try:
            run_once(exchange)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive on transient errors
            logging.error("An error occurred in main loop: %s", exc)
        time.sleep(LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
