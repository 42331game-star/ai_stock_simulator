import json
from typing import Any, Dict


def apply_stock_split(
    db,
    forum,
    symbol: str,
    split_ratio: int,
    tick: int,
    day: int,
    day_tick: int,
    phase: str,
    source: str = "manual",
    reason: str = "",
) -> Dict[str, Any]:
    if split_ratio < 2:
        raise ValueError("split_ratio must be at least 2")

    catalog = db.get_stock_catalog()
    if symbol not in catalog:
        raise ValueError(f"Unknown stock symbol: {symbol}")

    old_price = float(catalog[symbol]["price"])
    new_price = round(max(0.01, old_price / split_ratio), 2)

    changed_agents = 0
    total_new_shares = 0
    with db._get_connection() as conn:
        agents = conn.execute("SELECT id, cash, holdings FROM agents").fetchall()
        for row in agents:
            holdings = json.loads(row["holdings"] or "{}")
            current_qty = int(holdings.get(symbol, 0))
            if current_qty <= 0:
                continue
            new_qty = current_qty * split_ratio
            holdings[symbol] = new_qty
            conn.execute(
                "UPDATE agents SET holdings = ? WHERE id = ?",
                (json.dumps(holdings, ensure_ascii=False), row["id"]),
            )
            changed_agents += 1
            total_new_shares += new_qty - current_qty

    db.record_price(
        symbol=symbol,
        price=new_price,
        volume=0,
        tick=tick,
        day=day,
        day_tick=day_tick,
        phase=phase,
    )
    db.adjust_stock_fundamentals_for_split(symbol, split_ratio)

    db.record_stock_split_event(
        day=day,
        tick=tick,
        day_tick=day_tick,
        phase=phase,
        symbol=symbol,
        split_ratio=split_ratio,
        old_price=old_price,
        new_price=new_price,
        source=source,
        reason=reason,
    )

    prices = db.get_all_stock_prices()
    with db._get_connection() as conn:
        agent_rows = conn.execute("SELECT id, cash, holdings FROM agents").fetchall()
    for row in agent_rows:
        holdings = json.loads(row["holdings"] or "{}")
        stock_value = sum(float(prices.get(stock, 0.0)) * float(qty) for stock, qty in holdings.items())
        cash = round(float(row["cash"]), 2)
        db.record_asset_snapshot(
            agent_id=row["id"],
            tick=tick,
            day=day,
            day_tick=day_tick,
            phase=phase,
            source="stock_split",
            cash=cash,
            stock_value=round(stock_value, 2),
            total_assets=round(cash + stock_value, 2),
            holdings=holdings,
            note=f"{symbol} 1:{split_ratio} split ({source})",
        )

    summary = (
        f"{symbol} completed a 1:{split_ratio} stock split. "
        f"Price adjusted from ${old_price:,.2f} to ${new_price:,.2f}. "
        f"{changed_agents} agents had their holdings updated."
    )
    if forum is not None:
        forum.add_post("MarketDesk", summary, sentiment=0.15, is_hot=True)

    return {
        "symbol": symbol,
        "split_ratio": split_ratio,
        "old_price": old_price,
        "new_price": new_price,
        "changed_agents": changed_agents,
        "additional_shares_issued": total_new_shares,
        "source": source,
        "reason": reason,
        "summary": summary,
    }
