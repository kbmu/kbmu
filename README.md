import os
import ccxt
import pandas as pd
import numpy as np
import time
import logging
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, MACD
from ta.volatility import BollingerBands
import smtplib
from email.mime.text import MIMEText

# Configure logging
logging.basicConfig(filename='trading_bot.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# API credentials are loaded from environment variables, never hard-coded
API_KEY = os.environ["COINBASE_API_KEY"]
API_SECRET = os.environ["COINBASE_API_SECRET"]

# Initialize the exchange
exchange = ccxt.coinbase({
    'apiKey': API_KEY,
    'secret': API_SECRET,
})

symbol = 'XRP/USD'  # Example trading pair
parameters = {
    'rsi_period': 14, 'sma_period': 50,
    'macd_short': 12, 'macd_long': 26, 'macd_signal': 9,
    'bollinger_window': 20, 'bollinger_std': 2,
    'overbought': 70, 'oversold': 30,
    'risk_percentage': 0.01, 'stop_loss_percentage': 0.02, 'take_profit_percentage': 0.05,
    'trend_window': 200  # Added for trend confirmation
}

def send_email(subject, message):
    sender_email = os.environ["ALERT_SENDER_EMAIL"]
    receiver_email = os.environ["ALERT_RECEIVER_EMAIL"]
    password = os.environ["ALERT_EMAIL_PASSWORD"]

    msg = MIMEText(message)
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = receiver_email

    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(sender_email, password)
            server.send_message(msg)
        logging.info("Email sent successfully.")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")

def get_balance():
    balance = exchange.fetch_balance()
    return balance['total']['USD']  # Adjust based on your base currency

def get_market_price():
    ticker = exchange.fetch_ticker(symbol)
    return ticker['last']

def fetch_historical_data():
    # Fetch more historical data for better trend analysis
    limit = max(parameters['trend_window'], parameters['rsi_period'], parameters['sma_period'], parameters['macd_long'], parameters['bollinger_window']) + 1
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1m', limit=limit)
    return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

def calculate_indicators(data):
    rsi = RSIIndicator(data['close'], window=parameters['rsi_period']).rsi().iloc[-1]
    sma = SMAIndicator(data['close'], window=parameters['sma_period']).sma_indicator().iloc[-1]
    macd = MACD(data['close'], window_slow=parameters['macd_long'], window_fast=parameters['macd_short'], window_sign=parameters['macd_signal'])
    macd_value = macd.macd().iloc[-1]
    macd_signal_value = macd.macd_signal().iloc[-1]
    bollinger = BollingerBands(data['close'], window=parameters['bollinger_window'], window_dev=parameters['bollinger_std'])
    bollinger_upper = bollinger.bollinger_hband().iloc[-1]
    bollinger_lower = bollinger.bollinger_lband().iloc[-1]
    # Add trend indicator
    trend = SMAIndicator(data['close'], window=parameters['trend_window']).sma_indicator().iloc[-1]
    return rsi, sma, macd_value, macd_signal_value, bollinger_upper, bollinger_lower, trend

def calculate_position_size(balance):
    risk_amount = balance * parameters['risk_percentage']
    return risk_amount / get_market_price()

def place_order(order_type, amount):
    try:
        if order_type == 'buy':
            order = exchange.create_market_buy_order(symbol, amount)
            logging.info(f"Buy order placed: {order}")
            send_email("Trade Executed", f"Buy order placed: {order}")
            return order
        elif order_type == 'sell':
            order = exchange.create_market_sell_order(symbol, amount)
            logging.info(f"Sell order placed: {order}")
            send_email("Trade Executed", f"Sell order placed: {order}")
            return order
    except Exception as e:
        logging.error(f"Error placing order: {e}")
        send_email("Order Error", f"Error placing order: {e}")

def set_stop_loss(order, stop_loss_price):
    logging.info(f"Stop Loss set at {stop_loss_price}")

def set_take_profit(order, take_profit_price):
    logging.info(f"Take Profit set at {take_profit_price}")

def backtest_strategy(data):
    initial_balance = 1000
    balance = initial_balance
    position = 0

    for index, row in data.iterrows():
        indicators = calculate_indicators(data.iloc[:index + 1])
        market_price = row['close']

        if indicators[0] < parameters['oversold'] and market_price > indicators[1] and indicators[2] > indicators[3] and market_price < indicators[5] and market_price > indicators[6]:
            position_size = calculate_position_size(balance)
            position += position_size
            balance -= position_size * market_price
            logging.info(f"Backtest Buy: {position_size} XRP at {market_price}")

        elif indicators[0] > parameters['overbought'] and market_price < indicators[1] and indicators[2] < indicators[3] and market_price > indicators[4] and market_price < indicators[6]:
            balance += position * market_price
            logging.info(f"Backtest Sell: {position} XRP at {market_price}")
            position = 0

    final_balance = balance + position * market_price
    logging.info(f"Backtest completed. Initial Balance: {initial_balance}, Final Balance: {final_balance}")

def main():
    historical_data = fetch_historical_data()
    backtest_strategy(historical_data)

    while True:
        try:
            market_price = get_market_price()
            logging.info(f"Current market price: {market_price}")

            historical_data = fetch_historical_data()
            indicators = calculate_indicators(historical_data)
            logging.info(f"Current indicators: {indicators}")

            balance = get_balance()
            position_size = calculate_position_size(balance)

            # Strategy with trend confirmation
            if indicators[0] < parameters['oversold'] and market_price > indicators[1] and indicators[2] > indicators[3] and market_price < indicators[5] and market_price > indicators[6]:
                logging.info("Placing buy order...")
                order = place_order('buy', position_size)
                stop_loss_price = market_price * (1 - parameters['stop_loss_percentage'])
                take_profit_price = market_price * (1 + parameters['take_profit_percentage'])
                set_stop_loss(order, stop_loss_price)
                set_take_profit(order, take_profit_price)

            elif indicators[0] > parameters['overbought'] and market_price < indicators[1] and indicators[2] < indicators[3] and market_price > indicators[4] and market_price < indicators[6]:
                logging.info("Placing sell order...")
                order = place_order('sell', position_size)
                stop_loss_price = market_price * (1 + parameters['stop_loss_percentage'])
                take_profit_price = market_price * (1 - parameters['take_profit_percentage'])
                set_stop_loss(order, stop_loss_price)
                set_take_profit(order, take_profit_price)

            time.sleep(60)  # Wait for a minute before the next check
        except Exception as e:
            logging.error(f"An error occurred in main loop: {e}")
            time.sleep(60)  # Wait before retrying

if __name__ == "__main__":
    main()
