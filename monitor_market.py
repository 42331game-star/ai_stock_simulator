from pathlib import Path

from database import SimulationDB


PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "server_society.db"


def monitor():
    db = SimulationDB(str(DB_PATH))

    print("\n" + "=" * 60)
    print(f"{'Symbol':<10} | {'Current Price':<15} | {'Trend (Last 3)':<25}")
    print("-" * 60)

    with db._get_connection() as conn:
        stocks = conn.execute("SELECT symbol FROM stocks ORDER BY symbol").fetchall()

    for stock in stocks:
        symbol = stock["symbol"]
        price = db.get_stock_price(symbol)
        history = db.get_price_history(symbol, 3)
        prices = [f"{row['price']:.2f}" for row in history]
        trend_str = " -> ".join(prices) if prices else "No history"
        print(f"{symbol:<10} | ${price:<14.2f} | {trend_str:<25}")

    print("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        monitor()
    except Exception as e:
        print(f"Error reading database: {e}")
        print(f"Expected DB path: {DB_PATH}")
        print("Make sure the server has been started at least once.")
