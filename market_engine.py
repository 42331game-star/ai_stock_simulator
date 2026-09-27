import random
from typing import List, Dict, Any, Tuple, NamedTuple, Optional
from collections import defaultdict

class TradeResult(NamedTuple):
    """單筆交易結果"""
    agent_id: str
    stock: str
    action: str
    executed_amount: int
    refunded_amount: int  # 對買家是退款, 對賣家是退回股票

class MarketEngine:
    """
    AI Agentic Society - 市場撮合引擎 (含漲跌幅限制)
    """
    def __init__(self):
        # 市場固定股票設定: {symbol: (price, beta)}
        self.stock_config = {
            "VDA": {"price": 100.0, "beta": 0.02},
            "SMC": {"price": 50.0, "beta": 0.04},
            "SLA": {"price": 150.0, "beta": 0.07},
            "ITE": {"price": 30.0, "beta": 0.12},
        }
        self.day_open_prices = {} # 儲存每日開盤價，用於漲跌幅限制

    # Upper bound so a malformed order cannot explode the lottery ticket pool.
    MAX_ORDER_AMOUNT = 100_000

    def _sanitize_order(self, order: Any) -> Optional[Dict[str, Any]]:
        """
        Validate an order (orders originate from an LLM, so the shape is not trusted).
        Returns a normalized copy, or None when the order must be dropped.
        """
        if not isinstance(order, dict):
            return None
        stock = order.get("stock")
        if not isinstance(stock, str) or stock not in self.stock_config:
            return None
        action = str(order.get("action", "")).lower()
        if action not in ("buy", "sell"):
            return None
        amount = order.get("amount", 0)
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            return None
        amount = int(amount)
        if amount <= 0 or amount > self.MAX_ORDER_AMOUNT:
            return None
        agent_id = order.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            return None

        cleaned = dict(order)
        cleaned.update({"stock": stock, "action": action, "amount": amount})
        return cleaned

    def process_orders(self, orders: List[Dict[str, Any]]) -> Tuple[List[TradeResult], Dict[str, float]]:
        """
        執行完整結算流程
        :param orders: 訂單列表 [{"agent_id": "001", "stock": "VDA", "action": "buy", "amount": 5}, ...]
        :return: (結算報告, 更新後的股價字典)
        """
        # 步驟一：收單與分類
        # 分類格式: { "VDA": {"buy": [orders], "sell": [orders]} }
        categorized = defaultdict(lambda: {"buy": [], "sell": []})
        for order in orders:
            cleaned = self._sanitize_order(order)
            if cleaned is None:
                continue
            categorized[cleaned["stock"]][cleaned["action"]].append(cleaned)

        all_settlements = []
        new_prices = {}

        # 針對每檔股票獨立進行撮合與定價
        for symbol, pools in categorized.items():
            buy_orders = pools["buy"]
            sell_orders = pools["sell"]
            
            buy_vol = sum(o["amount"] for o in buy_orders)
            sell_vol = sum(o["amount"] for o in sell_orders)

            # 步驟二：搓合抽籤與退款
            stock_settlements = self._match_and_lottery(symbol, buy_orders, sell_orders, buy_vol, sell_vol)
            all_settlements.extend(stock_settlements)

            # 步驟三：計算新股價
            new_prices[symbol] = self._calculate_new_price(symbol, buy_vol, sell_vol)

        return all_settlements, new_prices

    def _match_and_lottery(self, symbol: str, buy_orders: List[Dict], sell_orders: List[Dict], buy_vol: int, sell_vol: int) -> List[TradeResult]:
        """
        實作 Closed-loop 抽籤機制
        """
        settlements = []
        matched_vol = min(buy_vol, sell_vol)
        
        # --- 買方處理 ---
        if buy_vol <= sell_vol:
            # 買單全部成交
            for o in buy_orders:
                settlements.append(TradeResult(o["agent_id"], symbol, "buy", o["amount"], 0))
        else:
            # 買單過多 -> 抽籤
            # 將所有買單展開成單張票券以確保公平隨機
            ticket_pool = []
            for o in buy_orders:
                ticket_pool.extend([o["agent_id"]] * o["amount"])
            
            winners = random.sample(ticket_pool, matched_vol)
            # 統計每位贏家拿到了幾張
            win_counts = defaultdict(int)
            for winner_id in winners:
                win_counts[winner_id] += 1
            
            for o in buy_orders:
                exec_amt = win_counts[o["agent_id"]]
                refund_amt = o["amount"] - exec_amt
                settlements.append(TradeResult(o["agent_id"], symbol, "buy", exec_amt, refund_amt))

        # --- 賣方處理 ---
        if sell_vol <= buy_vol:
            # 賣單全部成交
            for o in sell_orders:
                settlements.append(TradeResult(o["agent_id"], symbol, "sell", o["amount"], 0))
        else:
            # 賣單過多 -> 抽籤
            ticket_pool = []
            for o in sell_orders:
                ticket_pool.extend([o["agent_id"]] * o["amount"])
            
            winners = random.sample(ticket_pool, matched_vol)
            win_counts = defaultdict(int)
            for winner_id in winners:
                win_counts[winner_id] += 1
                
            for o in sell_orders:
                exec_amt = win_counts[o["agent_id"]]
                return_amt = o["amount"] - exec_amt
                settlements.append(TradeResult(o["agent_id"], symbol, "sell", exec_amt, return_amt))

        return settlements

    def _calculate_new_price(self, symbol: str, buy_vol: int, sell_vol: int) -> float:
        """
        根據供需失衡計算新價格 (含 Clamp 防呆、下市保護、及每日 ±10% 漲跌幅限制)
        """
        current_price = self.stock_config[symbol]["price"]
        beta = self.stock_config[symbol]["beta"]
        open_price = self.day_open_prices.get(symbol, current_price)

        # 1. 計算失衡倍率 (分母防零)
        if buy_vol == 0 and sell_vol == 0:
            # 【第二道防線】無量不漲跌：如果沒有任何真實買賣張數，價格維持原狀
            return current_price

        denominator = sell_vol if sell_vol > 0 else 1
        imbalance = (buy_vol - sell_vol) / denominator

        # 2. Clamp 限制在 [-5.0, 5.0] 之間
        imbalance = max(-5.0, min(5.0, imbalance))

        # 3. 計算新價格
        price_change_pct = imbalance * beta
        theoretical_price = current_price * (1 + price_change_pct)

        # 4. 漲跌幅限制 (Price Limit): $\pm 10\%$
        round_upper_limit = current_price * 1.10
        round_lower_limit = current_price * 0.90
        round_limited_price = max(round_lower_limit, min(round_upper_limit, theoretical_price))
        daily_upper_limit = open_price * 1.50
        daily_lower_limit = open_price * 0.50
        new_price = max(daily_lower_limit, min(daily_upper_limit, round_limited_price))

        # 5. 下市保護: 最低 0.01
        return max(0.01, round(new_price, 2))

if __name__ == "__main__":
    # --- 測試場景：買單遠大於賣單 ---
    engine = MarketEngine()
    
    # 模擬訂單：VDA 股票
    # 買方：Agent A(10張), Agent B(10張), Agent C(10張) -> 總買 30
    # 賣方：Agent D(5張) -> 總賣 5
    test_orders = [
        {"agent_id": "Agent_A", "stock": "VDA", "action": "buy", "amount": 10},
        {"agent_id": "Agent_B", "stock": "VDA", "action": "buy", "amount": 10},
        {"agent_id": "Agent_C", "stock": "VDA", "action": "buy", "amount": 10},
        {"agent_id": "Agent_D", "stock": "VDA", "action": "sell", "amount": 5},
    ]

    print("🚀 Testing MarketEngine - High Demand Scenario (Buy > Sell)")
    print(f"Initial VDA Price: {engine.stock_config['VDA']['price']}")
    print("-" * 60)

    settlements, prices = engine.process_orders(test_orders)

    print("📋 Settlement Report:")
    for s in settlements:
        status = "SUCCESS" if s.executed_amount == (s.executed_amount + s.refunded_amount) else "PARTIAL/REFUND"
        print(f"Agent {s.agent_id} | {s.action.upper()} {s.stock} | "
              f"Exec: {s.executed_amount}, Refund/Return: {s.refunded_amount} | [{status}]")

    print("-" * 60)
    print("📈 Price Updates:")
    for sym, price in prices.items():
        old_p = engine.stock_config[sym]["price"]
        diff = price - old_p
        print(f"{sym}: {old_p} -> {price} ({'+' if diff>0 else ''}{diff:.2f})")
