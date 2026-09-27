import json
import os

from database import SimulationDB


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    db = SimulationDB(os.path.join(base_dir, "server_society.db"))
    prices = db.get_all_stock_prices()
    report = db.run_health_check(prices=prices)

    print("=" * 72)
    print("AI STOCK SIMULATOR DB HEALTH CHECK")
    print("=" * 72)
    print(f"Status: {report['status'].upper()}")
    print(f"Checked Agents: {report['checked_agents']}")
    print(f"Negative Cash Agents: {report['negative_cash_agents']}")
    print(f"Negative Holding Entries: {report['negative_holding_entries']}")
    print(f"Asset Formula Errors: {report['asset_formula_errors']}")
    print(f"Total Cash: ${report['total_cash']:,.2f}")
    print(f"Total Stock Value: ${report['total_stock_value']:,.2f}")
    print(f"Total Assets: ${report['total_assets']:,.2f}")
    print("-" * 72)
    if report["issues"]:
        print("Issues:")
        for issue in report["issues"]:
            print(f"- [{issue['type']}] {issue['message']}")
    else:
        print("Issues: none")
    print("-" * 72)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
