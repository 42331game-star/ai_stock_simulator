
import os
import sys
import logging
import json
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from simulation_server import SimulationServer
from main_loop import VirtualClock

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("ReflectionTest")

def test_reflection():
    API_KEY = os.environ.get("MIMO_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    if not API_KEY:
        logger.error("No API key found. Set MIMO_API_KEY or GEMINI_API_KEY first.")
        return
    server = SimulationServer(api_key=API_KEY)
    server.setup_world()
    
    # Pick an agent that actually exists in this world (it ships without preset agents).
    with server.db._get_connection() as conn:
        row = conn.execute("SELECT id FROM agents ORDER BY id LIMIT 1").fetchone()
    if not row:
        logger.error("No agents found. Create at least one agent before running this test.")
        return
    test_aid = row["id"]
    
    # 1. 準備一些記憶數據，讓反思有內容可分析
    with server.db._get_connection() as conn:
        # 模擬一些交易記憶
        t_mem = [
            {"tick": 1, "stock": "SLA", "action": "buy", "exec": 10, "price": 150},
            {"tick": 2, "stock": "SLA", "action": "buy", "exec": 10, "price": 160}
        ]
        # 模擬一些社交記憶
        s_mem = [
            {"tick": 1, "feed_summary": "SLA is the future of robotics...", "decision": "post", "thought": "I agree!"}
        ]
        # 初始信念
        b_mem = {"SLA": "Interesting tech"}
        
        conn.execute(
            "UPDATE agents SET trading_memory = ?, social_memory = ?, belief_memory = ? WHERE id = ?",
            (json.dumps(t_mem), json.dumps(s_mem), json.dumps(b_mem), test_aid)
        )
    
    logger.info(f"🧪 Testing Reflection Round for {test_aid}...")
    
    # 2. 執行反思回合
    server.run_reflection_round()
    
    # 3. 驗證 belief_memory 是否被更新
    state = server.db.get_agent_state(test_aid)
    new_b_mem = state.get('belief_memory', '{}')
    if isinstance(new_b_mem, str): new_b_mem = json.loads(new_b_mem)
    
    logger.info(f"Old Beliefs: {b_mem}")
    logger.info(f"New Beliefs: {new_b_mem}")
    
    if new_b_mem != b_mem:
        logger.info("✅ Belief memory was updated successfully!")
    else:
        logger.warning("❌ Belief memory remained the same. Reflection might have failed or decided not to change.")

if __name__ == "__main__":
    test_reflection()
