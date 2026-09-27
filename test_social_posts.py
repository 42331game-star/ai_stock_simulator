
import os
import sys
import logging

# Add workspace to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from simulation_server import SimulationServer
from database import SimulationDB

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("SocialTest")

# Patch SimulationServer to print decisions
def patched_run_social_round(self):
    logger.info(f"💬 {datetime.now().strftime('%H:%M')} | Socializing phase...")
    with self.db._get_connection() as conn:
        agents = conn.execute("SELECT id, role FROM agents").fetchall()

    for agent in agents:
        aid, role = agent['id'], agent['role']
        state = self.db.get_agent_state(aid)
        wealth_context = f"Your Current Wealth: ${state['cash']:.2f} and holdings {state['holdings']}.\n"
        feed = self.forum.get_feed(aid)
        feed_text = "\n".join([f"PostID {p.post_id} by {p.agent_id}: {p.content}" for p in feed])
        prompt = f"{wealth_context}Feed:\n{feed_text}\nDecide to post or reply."
        decision = self.brain.think(aid, role, prompt)
        
        logger.info(f"Agent {aid} ({role}) decided: {decision}")
        
        if decision.get("action") == "post":
            self.forum.add_post(aid, decision.get("content", "No content"), 0.0, is_hot=(role=="Journalist"))
        elif decision.get("action") == "reply":
            self.forum.add_post(aid, decision.get("content", "No content"), 0.0, parent_id=decision.get("target_id"))

from datetime import datetime

def test_social_activity():
    API_KEY = os.environ.get("MIMO_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    if not API_KEY:
        logger.error("No API key found. Set MIMO_API_KEY or GEMINI_API_KEY first.")
        return
    logger.info("🚀 Starting Social Posting Test...")
    
    server = SimulationServer(api_key=API_KEY)
    server.setup_world()
    
    # Apply patch
    SimulationServer.run_social_round = patched_run_social_round
    
    logger.info("💬 Triggering one Social Round...")
    server.run_social_round()
    
    logger.info("✅ Social round completed. Checking forum_log.md...")
    
    log_path = "ai_stock_simulator/forum_log.md"
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            last_line = lines[-1] if lines else "Empty"
            logger.info(f"Last entry in forum log: {last_line.strip()}")

if __name__ == "__main__":
    test_social_activity()
