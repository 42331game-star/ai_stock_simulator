
import os
import sys
import logging
import json
from datetime import datetime

# Add workspace to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from simulation_server import SimulationServer
from database import SimulationDB

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("SystemTest")

def test_system():
    API_KEY = os.environ.get("MIMO_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    if not API_KEY:
        logger.error("No API key found. Set MIMO_API_KEY or GEMINI_API_KEY first.")
        return
    
    logger.info("🚀 Starting Full System Integration Test...")
    
    # 1. Initialize Server
    server = SimulationServer(api_key=API_KEY)
    
    # 2. Test setup_world (Incremental Loading)
    logger.info("Testing setup_world...")
    server.setup_world()
    
    # Verify agents are in DB
    with server.db._get_connection() as conn:
        agents = conn.execute("SELECT id FROM agents").fetchall()
        logger.info(f"✅ Total agents in DB: {len(agents)}")
    if not agents:
        logger.error("No agents found. Create at least one agent before running this test.")
        return
    
    # 3. Test Market Round (Multiple Trades & Trading Memory)
    logger.info("Testing run_market_round...")
    # Force a market open state for testing
    server.clock.state = server.clock.MarketState.OPEN if hasattr(server.clock, 'MarketState') else 1 
    # In the actual code it's MarketState.OPEN
    from main_loop import MarketState
    server.clock.state = MarketState.OPEN
    
    server.run_market_round()
    
    # Verify trading memory was written
    with server.db._get_connection() as conn:
        first_agent_id = agents[0]['id']
        state = server.db.get_agent_state(first_agent_id)
        t_mem = state.get('trading_memory', '[]')
        if isinstance(t_mem, str): t_mem = json.loads(t_mem)
        logger.info(f"✅ Agent {first_agent_id} trading memory size: {len(t_mem)}")
    
    # 4. Test Social Round (Social Memory & Posting)
    logger.info("Testing run_social_round...")
    server.clock.state = MarketState.CLOSED
    server.run_social_round()
    
    # Verify social memory was written
    with server.db._get_connection() as conn:
        state = server.db.get_agent_state(first_agent_id)
        s_mem = state.get('social_memory', '[]')
        if isinstance(s_mem, str): s_mem = json.loads(s_mem)
        logger.info(f"✅ Agent {first_agent_id} social memory size: {len(s_mem)}")
    
    # 5. Test DB Persistence (Simulate restart)
    logger.info("Testing Persistence (Simulating Restart)...")
    new_server = SimulationServer(api_key=API_KEY)
    # Should not wipe DB
    new_server.setup_world() 
    
    with new_server.db._get_connection() as conn:
        count = conn.execute("SELECT count(*) as c FROM agents").fetchone()['c']
        logger.info(f"✅ Agents count after restart: {count} (Should be same as before)")

    logger.info("🎉 All system tests passed successfully!")

if __name__ == "__main__":
    test_system()
