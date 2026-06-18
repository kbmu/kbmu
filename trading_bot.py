"""Algorithmic cryptocurrency trading bot.

Trades a configured pair on Coinbase via ccxt using a multi-indicator
technical-analysis strategy (RSI, SMA, MACD, Bollinger Bands + trend SMA).

Credentials and settings are read from environment variables (see .env.example).
By default the bot runs in DRY_RUN mode and does NOT place real orders.
"""

import os
import json
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

def _env_flag(name, default):
    """Parse a boolean-ish environment variable."""
    return os.environ.get(name, default).strip().lower() not in ("false", "0", "no")


# Safety switch: when truthy (the default), the bot logs intended trades but
# does NOT place real orders. Set DRY_RUN=false ONLY when you intend to trade
# real funds.
DRY_RUN = _env_flag("DRY_RUN", "true")

# When enabled, the bot places a server-side bracket order (take-profit limit +
# stop-loss trigger, OCO) at entry so the exchange enforces exits even if the bot
# is offline. UNVERIFIED against live Coinbase — test with minimal funds first.
# When disabled (the default), exits are enforced bot-side (price checked each
# cycle). See README "Exchange-side protective orders".
USE_EXCHANGE_PROTECTIVE_ORDERS = _env_flag("USE_EXCHANGE_PROTECTIVE_ORDERS", "false")

# File used to persist the open position so exits survive a restart.
POSITION_STATE_FILE = os.environ.get("POSITION_STATE_FILE", "position_state.json")

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
    # Backtest realism. fee_rate is per-side taker fee (Coinbase taker ~0.6%);
    # slippage is the fraction the fill price moves against you per trade.
    "fee_rate": 0.006,
    "slippage": 0.0005,
}

# Named indicator bundle so callers don't rely on positional tuple indexes.
Indicators = namedtuple(
    "Indicators",
    ["rsi", "sma", "macd", "macd_signal", "bb_upper", "bb_lower", "trend"],
)

# An open long position and its protective exit levels. The bot is long-only:
# it buys to open and sells to close. ``protective_order_ids`` holds any
# exchange-side bracket/OCO order ids when USE_EXCHANGE_PROTECTIVE_ORDERS is on.
Position = namedtuple(
    "Position",
    ["size", "entry_price", "stop_loss", "take_profit", "protective_order_ids"],
)
Position.__new__.__defaults__ = ((),)  # protective_order_ids defaults to empty


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


def buy_fill_price(price):
    """Backtest buy fill, moved up by slippage (worse for the buyer)."""
    return price * (1 + parameters["slippage"])


def sell_fill_price(price):
    """Backtest sell fill, moved down by slippage (worse for the seller)."""
    return price * (1 - parameters["slippage"])


def trade_fee(notional):
    """Per-side fee on a trade's notional value."""
    return abs(notional) * parameters["fee_rate"]


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


def protective_levels(entry_price):
    """Stop-loss and take-profit prices for a long opened at entry_price."""
    stop_loss = entry_price * (1 - parameters["stop_loss_percentage"])
    take_profit = entry_price * (1 + parameters["take_profit_percentage"])
    return stop_loss, take_profit


def evaluate(position, ind, price, exchange_managed_exits=False):
    """Decide the action for this cycle.

    Returns a ``(action, reason)`` pair where action is one of ``"open"``,
    ``"close"``, or ``"hold"``. Protective exits (stop-loss / take-profit) take
    priority over strategy signals when a position is open.

    When ``exchange_managed_exits`` is True, the stop-loss / take-profit checks
    are skipped because a server-side bracket order owns those exits; only a
    discretionary sell signal triggers a close here.
    """
    if position is None:
        if should_buy(ind, price):
            return "open", "buy_signal"
        return "hold", None

    if not exchange_managed_exits:
        if price <= position.stop_loss:
            return "close", "stop_loss"
        if price >= position.take_profit:
            return "close", "take_profit"
    if should_sell(ind, price):
        return "close", "sell_signal"
    return "hold", None


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


# --------------------------------------------------------------------------- #
# Exchange-side protective (bracket) orders
#
# NOTE: This path places real OCO/bracket orders and is UNVERIFIED against live
# Coinbase. It is opt-in via USE_EXCHANGE_PROTECTIVE_ORDERS and is best-effort:
# any failure falls back to bot-side monitoring. Verify with minimal funds.
# --------------------------------------------------------------------------- #

def place_protective_orders(exchange, size, stop_loss, take_profit):
    """Place a server-side bracket (take-profit limit + stop-loss) sell order.

    Returns a list of placed order ids (empty if none were placed). A bracket
    order is OCO: whichever side triggers cancels the other, exchange-side.
    """
    if size <= 0:
        return []

    if DRY_RUN:
        logging.info(
            "[DRY_RUN] Would place bracket sell: size=%s take_profit=%s stop_loss=%s",
            size,
            take_profit,
            stop_loss,
        )
        return []

    try:
        # ccxt unified bracket: a take-profit limit sell with an attached
        # stop-loss trigger. Coinbase maps this to a trigger_bracket order.
        order = exchange.create_order(
            SYMBOL,
            "limit",
            "sell",
            size,
            take_profit,
            {"stopLossPrice": stop_loss},
        )
        order_id = order.get("id") if isinstance(order, dict) else None
        logging.info("Bracket protective order placed: %s", order)
        return [order_id] if order_id else []
    except Exception as exc:  # noqa: BLE001
        logging.error(
            "Failed to place exchange-side protective order (falling back to "
            "bot-side monitoring): %s",
            exc,
        )
        return []


def cancel_protective_orders(exchange, order_ids):
    """Best-effort cancellation of outstanding protective orders."""
    for order_id in order_ids or ():
        if not order_id or DRY_RUN:
            continue
        try:
            exchange.cancel_order(order_id, SYMBOL)
            logging.info("Cancelled protective order %s", order_id)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Could not cancel protective order %s: %s", order_id, exc)


def protective_order_filled(exchange, order_ids):
    """Return True if a protective order has already closed the position.

    Returns None if status can't be determined, so callers can fall back to
    bot-side monitoring rather than assuming a state.
    """
    if not order_ids or DRY_RUN:
        return False
    for order_id in order_ids:
        if not order_id:
            continue
        try:
            order = exchange.fetch_order(order_id, SYMBOL)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Could not fetch protective order %s: %s", order_id, exc)
            return None
        if order.get("status") in ("closed", "filled"):
            return True
    return False


# --------------------------------------------------------------------------- #
# Position persistence (so exits survive a restart)
# --------------------------------------------------------------------------- #

def save_position(position, path=None):
    """Persist the open position to disk, or remove the file when flat."""
    path = path or POSITION_STATE_FILE
    try:
        if position is None:
            if os.path.exists(path):
                os.remove(path)
            return
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(position._asdict(), handle)
    except OSError as exc:
        logging.warning("Could not persist position state: %s", exc)


def load_position(path=None):
    """Load a persisted position, or None if there isn't one."""
    path = path or POSITION_STATE_FILE
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        data["protective_order_ids"] = tuple(data.get("protective_order_ids", ()))
        position = Position(**data)
        logging.info("Restored open position from %s: %s", path, position)
        return position
    except (OSError, ValueError, TypeError) as exc:
        logging.warning("Could not load persisted position (%s); starting flat.", exc)
        return None


def backtest_strategy(data):
    """Replay the strategy over historical candles using only historical data.

    Models the same long-only state machine as live trading, including
    stop-loss / take-profit exits. Each candle's close is the reference price;
    fills include slippage and a per-side fee (see the `fee_rate` / `slippage`
    parameters). It still makes no network calls.
    """
    initial_balance = 1000.0
    balance = initial_balance
    position = None
    market_price = None

    # Start once enough candles exist for the longest-window indicator.
    warmup = parameters["trend_window"]
    for index in range(warmup, len(data)):
        window = data.iloc[: index + 1]
        ind = calculate_indicators(window)
        market_price = window["close"].iloc[-1]

        action, reason = evaluate(position, ind, market_price)
        if action == "open":
            fill = buy_fill_price(market_price)
            size = position_size_for(balance, fill)
            if size > 0:
                notional = size * fill
                balance -= notional + trade_fee(notional)
                stop_loss, take_profit = protective_levels(market_price)
                position = Position(size, market_price, stop_loss, take_profit)
                logging.info("Backtest Buy: %s %s at %s (incl. fees)", size, SYMBOL, fill)
        elif action == "close":
            fill = sell_fill_price(market_price)
            notional = position.size * fill
            balance += notional - trade_fee(notional)
            logging.info(
                "Backtest Sell (%s): %s %s at %s (incl. fees)",
                reason,
                position.size,
                SYMBOL,
                fill,
            )
            position = None

    if market_price is None:
        logging.warning("Backtest skipped: not enough historical data for warmup.")
        return None

    if position:
        # Liquidate any open position at the final price for reporting.
        fill = sell_fill_price(market_price)
        notional = position.size * fill
        balance += notional - trade_fee(notional)
    final_balance = balance
    logging.info(
        "Backtest completed. Initial Balance: %s, Final Balance: %s",
        initial_balance,
        final_balance,
    )
    return final_balance


def run_once(exchange, position):
    """Execute a single decision cycle, returning the (possibly new) position."""
    market_price = get_market_price(exchange)
    logging.info("Current market price: %s", market_price)

    # If the exchange owns the exits, first reconcile: a triggered bracket may
    # have already closed the position server-side.
    exchange_managed = USE_EXCHANGE_PROTECTIVE_ORDERS and position is not None and bool(
        position.protective_order_ids
    )
    if exchange_managed:
        filled = protective_order_filled(exchange, position.protective_order_ids)
        if filled is True:
            logging.info("Exchange protective order filled; position closed.")
            return None
        if filled is None:
            # Status unknown — fall back to bot-side monitoring this cycle.
            exchange_managed = False

    historical_data = fetch_historical_data(exchange)
    ind = calculate_indicators(historical_data)
    logging.info("Current indicators: %s", ind)

    action, reason = evaluate(position, ind, market_price, exchange_managed_exits=exchange_managed)

    if action == "open":
        balance = get_balance(exchange)
        size = position_size_for(balance, market_price)
        logging.info("Buy signal: opening position of %s %s", size, SYMBOL)
        order = place_order(exchange, "buy", size)
        if order is not None:
            stop_loss, take_profit = protective_levels(market_price)
            protective_ids = ()
            if USE_EXCHANGE_PROTECTIVE_ORDERS:
                protective_ids = tuple(
                    place_protective_orders(exchange, size, stop_loss, take_profit)
                )
            position = Position(size, market_price, stop_loss, take_profit, protective_ids)
            logging.info(
                "Position opened at %s. Stop loss: %s, take profit: %s",
                market_price,
                stop_loss,
                take_profit,
            )
    elif action == "close":
        logging.info("Exit signal (%s): closing position.", reason)
        # Cancel any exchange-side protective orders first so the balance is free
        # to sell and we don't leave a dangling resting order.
        cancel_protective_orders(exchange, position.protective_order_ids)
        order = place_order(exchange, "sell", position.size)
        if order is not None:
            position = None
    else:
        logging.info("No action this cycle (holding=%s).", position is not None)

    return position


def main():
    if DRY_RUN:
        logging.info("Starting in DRY_RUN mode: no real orders will be placed.")
    else:
        logging.warning("DRY_RUN is OFF: real orders WILL be placed with real funds.")
    if USE_EXCHANGE_PROTECTIVE_ORDERS:
        logging.info("Exchange-side protective (bracket) orders are ENABLED.")

    exchange = build_exchange()

    historical_data = fetch_historical_data(exchange)
    backtest_strategy(historical_data)

    # Restore any position from a previous run so exits survive a restart.
    position = load_position()
    while True:
        try:
            position = run_once(exchange, position)
            save_position(position)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive on transient errors
            logging.error("An error occurred in main loop: %s", exc)
        time.sleep(LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
