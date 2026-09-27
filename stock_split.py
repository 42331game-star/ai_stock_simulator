import argparse
import json
import os

from database import SimulationDB
from forum_system import VirtualForum
from stock_split_service import apply_stock_split


def main():
    parser = argparse.ArgumentParser(description="Apply a manual stock split inside the running market.")
    parser.add_argument("symbol", help="Stock symbol to split, e.g. VDA")
    parser.add_argument("--ratio", type=int, default=2, help="Split ratio, default 2 means 1:2")
    parser.add_argument("--reason", default="Manual operator stock split", help="Reason shown in logs and monitoring")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    db = SimulationDB(os.path.join(base_dir, "server_society.db"))
    forum = VirtualForum(db)
    runtime = db.get_runtime_state()

    result = apply_stock_split(
        db=db,
        forum=forum,
        symbol=args.symbol.upper(),
        split_ratio=args.ratio,
        tick=int(runtime.get("current_tick", 0)),
        day=int(runtime.get("day_count", 1)),
        day_tick=int(runtime.get("day_tick", 0)),
        phase=str(runtime.get("phase", "OPEN")),
        source="manual_script",
        reason=args.reason,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
