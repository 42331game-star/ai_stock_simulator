import random
import json
import logging
from typing import List, Dict, Any

# 匯入我們之前建立的所有模組
from database import SimulationDB
from main_loop import VirtualClock, MarketState
from llm_router import AgentBrain
from forum_system import VirtualForum
from market_engine import MarketEngine, TradeResult

# 設定日誌輸出，讓我們能清楚看到數據流
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("MVP_Test")

class AgenticSocietyMVP:
    """
    AI Agentic Society - 整合測試控制器
    將所有模組串接在一起，模擬一個微型虛擬社會
    """
    def __init__(self):
        # 1. 初始化核心組件
        self.db = SimulationDB("mvp_society.db")
        self.clock = VirtualClock()
        self.brain = AgentBrain()
        self.forum = VirtualForum()
        self.engine = MarketEngine()
        
        self.BASE_LIVING_COST = 50.0 # 每日基礎生活費

    def setup_world(self):
        """初始化世界：股票與 Agent"""
        logger.info("🌐 Initializing World State...")
        
        # A. 初始化股票
        stocks = [
            ("VDA", 100.0, 0.02), ("ITE", 30.0, 0.12), 
            ("SLA", 150.0, 0.07), ("SMC", 50.0, 0.04)
        ]
        for sym, price, beta in stocks:
            self.db.init_stock(sym, price, beta)
            # 同時同步到 Engine 的 config 中
            self.engine.stock_config[sym] = {"price": price, "beta": beta}

        # B. 初始化 10 個 Agent
        # 角色配置: (數量, 初始現金, 生活成本乘數, 角色名稱)
        role_configs = [
            (1, 100000.0, 0.5, "Boss"),
            (1, 20000.0, 1.0, "Journalist"),
            (3, 15000.0, 1.2, "MiddleClass"),
            (5, 5000.0, 2.0, "RetailInvestor")
        ]
        
        agent_id_counter = 1
        for count, cash, multiplier, role in role_configs:
            for _ in range(count):
                aid = f"Agent_{agent_id_counter:03d}"
                with self.db._get_connection() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO agents (id, role, cash, cost_multiplier) VALUES (?, ?, ?, ?)",
                        (aid, role, cash, multiplier)
                    )
                agent_id_counter += 1
        
        logger.info(f"✅ World initialized with {agent_id_counter-1} agents and 4 stocks.\n")

    def run_market_phase(self):
        """盤中模式：決策 -> 搓合 -> 更新"""
        logger.info(f"📈 --- Market OPEN Phase (Tick {self.clock.day_tick}) ---")
        
        # 1. 收集所有 Agent 的訂單
        all_orders = []
        # 獲取當前所有股價作為 Prompt 上下文
        current_prices = {s: self.db.get_stock_price(s) for s in self.engine.stock_config.keys()}
        
        # 模擬 10 個 Agent 思考
        with self.db._get_connection() as conn:
            agents = conn.execute("SELECT id, role FROM agents").fetchall()

        for agent in agents:
            aid, role = agent['id'], agent['role']
            # 構建 Prompt
            prompt = f"You are a {role}. Current prices: {current_prices}. Decide your trade."
            # 呼叫 Brain 獲取 JSON 決策
            decision = self.brain.think(prompt)
            
            if decision["action"] in ["buy", "sell"]:
                # 隨機分配一檔股票 (簡化 MVP 邏輯)
                stock = random.choice(list(current_prices.keys()))
                all_orders.append({
                    "agent_id": aid, 
                    "stock": stock, 
                    "action": decision["action"], 
                    "amount": decision["amount"] if decision["amount"] > 0 else 1
                })

        # 2. 丟進 MarketEngine 搓合
        settlements, new_prices = self.engine.process_orders(all_orders)

        # 3. 更新資料庫 (資金與持倉)
        for s in settlements:
            price = self.db.get_stock_price(s.stock)
            if s.action == "buy":
                cost = s.executed_amount * price
                self.db.update_agent_cash(s.agent_id, -cost)
                self.db.update_agent_holdings(s.agent_id, s.stock, s.executed_amount)
            else:
                gain = s.executed_amount * price
                self.db.update_agent_cash(s.agent_id, gain)
                self.db.update_agent_holdings(s.agent_id, s.stock, -s.executed_amount)

        # 4. 更新股價至 DB 並同步回 Engine
        for sym, price in new_prices.items():
            self.db.record_price(sym, price, volume=0, tick=self.clock.current_tick)
            self.engine.stock_config[sym]["price"] = price

        logger.info(f"Settled {len(settlements)} trades. Prices updated.")

    def run_social_phase(self):
        """盤後模式：閱讀 -> 發文"""
        logger.info(f"💬 --- Market CLOSED Phase (Tick {self.clock.day_tick}) ---")
        
        with self.db._get_connection() as conn:
            agents = conn.execute("SELECT id, role FROM agents").fetchall()

        for agent in agents:
            aid, role = agent['id'], agent['role']
            # 1. 閱讀論壇 Feed
            feed = self.forum.get_feed(aid)
            feed_text = "\n".join([f"{p.agent_id}: {p.content}" for p in feed])
            
            # 2. 思考如何回覆/發文
            prompt = f"You are a {role}. You read these posts:\n{feed_text}\nWhat is your opinion?"
            decision = self.brain.think(prompt)
            
            # 3. 發文 (MVP 簡化：將 thought_process 當作發文內容)
            sentiment = random.uniform(-1, 1)
            is_hot = (role == "Journalist" and random.random() > 0.7) # 記者有更高機率發熱門文
            self.forum.add_post(aid, decision["thought_process"], sentiment, is_hot)

        logger.info(f"Processed social interactions for {len(agents)} agents.")

    def apply_daily_costs(self):
        """每日結算：扣除生活費"""
        logger.info("💰 --- Daily Settlement: Deducting Living Costs ---")
        with self.db._get_connection() as conn:
            agents = conn.execute("SELECT id, cost_multiplier FROM agents").fetchall()
            
            for agent in agents:
                aid, mult = agent['id'], agent['cost_multiplier']
                cost = self.BASE_LIVING_COST * mult
                self.db.update_agent_cash(aid, -cost)
                
                # 檢查破產
                state = self.db.get_agent_state(aid)
                if state['cash'] < 0:
                    logger.warning(f"🚨 BANKRUPTCY ALERT: {aid} is broke! Cash: {state['cash']:.2f}")

    def simulate_day(self):
        """模擬完整的一天"""
        for _ in range(self.clock.TICK_PER_DAY):
            # 1. 推進時鐘
            self.clock.tick()
            
            # 2. 根據狀態執行不同邏輯
            if self.clock.state == MarketState.OPEN:
                self.run_market_phase()
            else:
                self.run_social_phase()
            
            # 3. 每日 24:00 (最後一個 Tick) 執行扣款
            if self.clock.day_tick == self.clock.TICK_PER_DAY - 1:
                self.apply_daily_costs()
        
        logger.info(f"✨ Day {self.clock.day_count} completed.\n")

if __name__ == "__main__":
    mvp = AgenticSocietyMVP()
    mvp.setup_world()
    
    # 運行 1 天的完整生命週期
    mvp.simulate_day()
    
    # 最後檢查結果
    logger.info("📊 Final World State Check:")
    with mvp.db._get_connection() as conn:
        final_agents = conn.execute("SELECT id, cash FROM agents").fetchall()
        for a in final_agents:
            logger.info(f"Agent {a['id']} Final Cash: {a['cash']:.2f}")
