
import os
import sys
import logging
import json
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from simulation_server import SimulationServer
from main_loop import MarketState

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("SingleAgentTest")

def test_single_agent():
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
    
    logger.info(f"🧪 Testing Single Agent: {test_aid}")
    
    # 1. 測試市場回合 (僅限該 Agent)
    logger.info("--- Testing Market Round ---")
    server.clock.state = MarketState.OPEN
    
    # 為了只測試一個 Agent，我們暫時 override run_market_round 的邏輯或直接模擬
    # 這裡我們用一個簡單的 lambda-like 方式：
    # 我們直接調用 think 並執行結算，不跑整個迴圈
    
    state = server.db.get_agent_state(test_aid)
    traits = server.agent_traits.get(test_aid, "Standard investor")
    market_context = server._get_market_context()
    
    # 模擬 run_market_round 內的 prompt 構造
    t_mem = state.get('trading_memory', '[]')
    if isinstance(t_mem, str): t_mem = json.loads(t_mem)
    s_mem = state.get('social_memory', '[]')
    if isinstance(s_mem, str): s_mem = json.loads(s_mem)
    b_mem = state.get('belief_memory', '{}')
    if isinstance(b_mem, str): b_mem = json.loads(b_mem)
    
    memory_context = (
        f"Your Trading Memory (Recent): {t_mem[-3:] if t_mem else 'None'}\n"
        f"Your Social Memory (Recent): {s_mem[-3:] if s_mem else 'None'}\n"
        f"Your Core Beliefs: {b_mem}\n"
    )
    portfolio_context = f"Your Wallet: ${state['cash']:.2f} | Your Holdings: {state['holdings']}\n"
    
    prompt = (
        f"Your Traits: {traits}\n"
        f"--- Your Memories ---\n{memory_context}\n"
        f"{portfolio_context}{market_context}\n"
        f"Instructions: Analyze the company visions and current prices. "
        f"As a {state['role']}, which company's vision aligns with your values or financial goals?\n\n"
        f"【交易指令輸出規範】\n"
        f"請回傳 JSON 格式，包含一個 'trades' 列表：\n"
        f"{{'trades': [{{'action': 'buy'|'sell'|'hold', 'stock': '...', 'amount': X}}, ...], 'thought_process': '...'}}\n"
    )
    
    decision = server.brain.think(test_aid, state['role'], prompt)
    logger.info(f"🤖 Market Decision: {decision}")
    
    # 2. 測試社交回合 (僅限該 Agent)
    logger.info("--- Testing Social Round ---")
    server.clock.state = MarketState.CLOSED
    
    wealth_context = f"Your Current Wealth: ${state['cash']:.2f} and holdings {state['holdings']}.\n"
    feed = server.forum.get_feed(test_aid)
    feed_text = "\n".join([f"PostID {p.post_id} by {p.agent_id}: {p.content}" for p in feed])
    
    s_mem = state.get('social_memory', '[]')
    if isinstance(s_mem, str): s_mem = json.loads(s_mem)
    
    social_prompt = (
        f"Your Traits: {traits}\n"
        f"Your Social Memory (Recent): {s_mem[-3:] if s_mem else 'None'}\n"
        f"{wealth_context}Feed:\n{feed_text}\n\n"
        f"Instructions: You are in SOCIAL PHASE. No trading. Decide to post, reply or hold.\n"
        f"Return JSON: {{'action': 'post'|'reply'|'hold', 'content': '...', 'target_id': ..., 'thought_process': '...'}}"
    )
    
    social_decision = server.brain.think(test_aid, state['role'], social_prompt)
    logger.info(f"💬 Social Decision: {social_decision}")
    
    logger.info("✅ Single agent test sequence completed.")

if __name__ == "__main__":
    test_single_agent()
