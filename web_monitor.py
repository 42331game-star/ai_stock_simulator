import hmac
import json
import logging
import os
import re
from collections import defaultdict
from typing import Dict, List
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request

from database import SimulationDB
from forum_system import VirtualForum

logger = logging.getLogger("web_monitor")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB request bodies are plenty for this API.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "server_society.db")
AGENTS_DIR = os.path.join(BASE_DIR, "agents")
TRADE_LOG_PATH = os.path.join(BASE_DIR, "trade_log.md")
FORUM_LOG_PATH = os.path.join(BASE_DIR, "forum_log.md")
STATUS_PATH = os.path.join(BASE_DIR, "simulation_status.json")
SIMULATION_STDERR_PATH = os.path.join(BASE_DIR, "simulation_stderr.log")

# Binding / mode are opt-in: loopback + no debugger unless explicitly enabled.
WEB_MONITOR_HOST = os.environ.get("WEB_MONITOR_HOST", "127.0.0.1")
WEB_MONITOR_DEBUG = os.environ.get("WEB_MONITOR_DEBUG", "0").strip().lower() in {"1", "true", "yes"}
# Optional shared secret for write endpoints. When set, clients must send it in X-Dashboard-Token.
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "").strip()

# Agent ids are used to build file paths; keep them strictly path-safe.
AGENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fff-]+$")

try:
    db = SimulationDB(DB_PATH)
except Exception:
    logger.exception("Database init failed")
    db = None


def internal_error_response():
    """Generic 500 body: exception details stay in the server log only."""
    return jsonify({"error": "Internal server error. Check the dashboard log for details."}), 500


@app.before_request
def guard_write_endpoints():
    """Reject cross-site and unauthenticated state-changing requests."""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None

    # Simple HTML forms cannot set custom headers, so this blocks CSRF by itself.
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return jsonify({"error": "Cross-site request rejected: missing X-Requested-With header."}), 403

    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if origin:
        origin_host = (urlparse(origin).hostname or "").lower()
        request_host = (request.host.split("@")[-1].split(":")[0] or "").lower()
        if origin_host and origin_host != request_host:
            return jsonify({"error": "Cross-site request rejected: origin mismatch."}), 403

    if DASHBOARD_TOKEN:
        provided = request.headers.get("X-Dashboard-Token", "")
        if not hmac.compare_digest(provided.encode("utf-8"), DASHBOARD_TOKEN.encode("utf-8")):
            return jsonify({"error": "Invalid or missing dashboard token."}), 401
    return None


def get_last_lines(file_path, num_lines=20):
    if not os.path.exists(file_path):
        return ["Log file not yet created."]
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return [line.strip() for line in lines[-num_lines:]]
    except Exception:
        logger.exception("Failed to read log file")
        return ["Error reading log file. See the dashboard log for details."]


def build_trade_feed(rows: List[Dict]) -> List[str]:
    if not rows:
        return []
    lines = []
    for row in rows[-20:]:
        lines.append(
            f"D{row['day']} T{row['tick']} | {row['agent_id']} | "
            f"{str(row['action']).upper()} {row['stock']} x{row['executed_amount']} @ ${float(row['price']):.2f} | "
            f"cash ${float(row['cash_before']):,.2f} -> ${float(row['cash_after']):,.2f}"
        )
    return lines


def build_forum_feed(rows: List[Dict]) -> List[str]:
    if not rows:
        return []
    lines = []
    for row in rows[-20:]:
        prefix = "[HOT] " if int(row.get("is_hot", 0)) else ""
        if row.get("parent_id") is not None:
            prefix += f"[Reply to {row['parent_id']}] "
        lines.append(f"{prefix}{row['agent_id']}: {row['content']}")
    return lines


def append_bulletin_memory_to_all_agents(bulletin_entry: Dict):
    with db._get_connection() as conn:
        agents = conn.execute("SELECT id, bulletin_memory FROM agents WHERE is_active = 1").fetchall()
        for agent in agents:
            try:
                existing = json.loads(agent["bulletin_memory"] or "[]")
            except json.JSONDecodeError:
                existing = []
            existing.append(dict(bulletin_entry))
            existing = existing[-8:]
            conn.execute(
                "UPDATE agents SET bulletin_memory = ? WHERE id = ?",
                (json.dumps(existing, ensure_ascii=False), agent["id"]),
            )


def extract_submitted_orders_from_log(file_path: str, limit: int = 24) -> List[str]:
    if not os.path.exists(file_path):
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
    except Exception:
        return []

    collected: List[str] = []
    in_current_market_block = False
    for line in reversed(lines):
        if "Trading round start." in line:
            in_current_market_block = True
            continue
        if not in_current_market_block:
            continue
        if "Sleeping for " in line or "Socializing phase..." in line or "Reflection phase" in line:
            break
        if "[Market]" in line:
            collected.append(line)
            if len(collected) >= limit:
                break
    return list(reversed(collected))


def build_matched_trades(rows: List[Dict]) -> List[str]:
    if not rows:
        return []
    latest_tick = max(int(row["tick"]) for row in rows)
    latest_rows = [row for row in rows if int(row["tick"]) == latest_tick]
    latest_rows.sort(key=lambda item: (item["agent_id"], item["stock"], item["action"]))
    lines = []
    for row in latest_rows:
        lines.append(
            f"D{row['day']} T{row['tick']} | {row['agent_id']} | "
            f"{str(row['action']).upper()} {row['stock']} x{row['executed_amount']} @ ${float(row['price']):.2f}"
        )
    return lines


def parse_holdings(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return {}


def read_json_file(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def build_daily_candles(rows: List[Dict]) -> List[Dict]:
    grouped: Dict[int, List[Dict]] = defaultdict(list)
    for row in rows:
        grouped[int(row["day"])].append(row)

    candles = []
    for day in sorted(grouped.keys()):
        items = grouped[day]
        prices = [float(item["price"]) for item in items]
        candles.append(
            {
                "label": f"Day {day}",
                "day": day,
                "open": prices[0],
                "high": max(prices),
                "low": min(prices),
                "close": prices[-1],
                "volume": int(sum(float(item.get("volume", 0)) for item in items)),
            }
        )
    return candles[-60:]


def build_intraday_candles(rows: List[Dict], block_size: int = 3) -> List[Dict]:
    bars = []
    for index in range(0, len(rows), block_size):
        block = rows[index:index + block_size]
        if not block:
            continue
        prices = [float(item["price"]) for item in block]
        start = block[0]
        end = block[-1]
        bars.append(
            {
                "label": f"D{start['day']} T{start['day_tick']}-{end['day_tick']}",
                "day": int(start["day"]),
                "open": prices[0],
                "high": max(prices),
                "low": min(prices),
                "close": prices[-1],
                "volume": int(sum(float(item.get("volume", 0)) for item in block)),
            }
        )
    return bars[-80:]


def build_agent_id(display_name: str) -> str:
    safe_name = re.sub(r"[^\w]+", "_", (display_name or "").strip(), flags=re.UNICODE).strip("_")
    safe_name = safe_name[:32] if safe_name else "User"
    existing = set()
    if os.path.isdir(AGENTS_DIR):
        for filename in os.listdir(AGENTS_DIR):
            if filename.endswith(".json"):
                existing.add(filename[:-5])
    sequence = 1
    while True:
        candidate = f"Agent_User_{sequence:03d}_{safe_name}"
        if candidate not in existing:
            return candidate
        sequence += 1


def write_agent_config(agent_id: str, role: str, cash: float, salary: float, cost_multiplier: float, traits: str):
    os.makedirs(AGENTS_DIR, exist_ok=True)
    payload = {
        "id": agent_id,
        "role": role,
        "cash": round(float(cash), 2),
        "salary": round(float(salary), 2),
        "cost_multiplier": round(float(cost_multiplier), 2),
        "traits": traits,
    }
    with open(os.path.join(AGENTS_DIR, f"{agent_id}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def remove_agent_config(agent_id: str):
    if not AGENT_ID_PATTERN.match(agent_id or ""):
        raise ValueError("Invalid agent id.")
    os.makedirs(AGENTS_DIR, exist_ok=True)
    config_path = os.path.join(AGENTS_DIR, f"{agent_id}.json")
    # Defence in depth: never touch a file outside the agents directory.
    if os.path.commonpath([os.path.realpath(config_path), os.path.realpath(AGENTS_DIR)]) != os.path.realpath(AGENTS_DIR):
        raise ValueError("Invalid agent id.")
    if os.path.isfile(config_path):
        os.remove(config_path)


@app.route("/")
def index():
    return render_template("index.html", dashboard_token=DASHBOARD_TOKEN)


@app.route("/api/health")
def health():
    return jsonify({"status": "healthy", "db_exists": os.path.exists(DB_PATH)})


@app.route("/api/data")
def get_data():
    if db is None:
        return jsonify({"error": "Database connection not established"}), 500

    try:
        prices = db.get_all_stock_prices()
        stock_catalog = db.get_stock_catalog()

        wealth = []
        with db._get_connection() as conn:
            agents = conn.execute("SELECT id, role, is_active, cash, holdings FROM agents WHERE is_active = 1").fetchall()
            for agent in agents:
                holdings = parse_holdings(agent["holdings"])
                stock_value = 0.0
                for symbol, quantity in holdings.items():
                    stock_value += float(prices.get(symbol, 0.0)) * float(quantity)

                cash = round(float(agent["cash"]), 2)
                stock_value = round(stock_value, 2)
                total_assets = round(cash + stock_value, 2)
                wealth.append(
                    {
                        "id": agent["id"],
                        "role": agent["role"],
                        "is_active": bool(agent["is_active"]),
                        "cash": cash,
                        "stock_value": stock_value,
                        "total_assets": total_assets,
                        "holdings": holdings,
                    }
                )

        wealth.sort(key=lambda item: item["total_assets"], reverse=True)
        wealth_by_id = {agent["id"]: agent for agent in wealth}
        agent_ids = [agent["id"] for agent in wealth]

        asset_history = {}
        for agent_id in agent_ids:
            history = db.get_asset_history(agent_id, limit=160)
            if not history and agent_id in wealth_by_id:
                current = wealth_by_id[agent_id]
                history = [
                    {
                        "tick": 0,
                        "day": 1,
                        "day_tick": 0,
                        "phase": "UNKNOWN",
                        "source": "current_state",
                        "cash": current["cash"],
                        "stock_value": current["stock_value"],
                        "total_assets": current["total_assets"],
                        "holdings": current["holdings"],
                        "note": "Current snapshot fallback",
                        "timestamp": "",
                    }
                ]
            asset_history[agent_id] = history
        trade_asset_log = db.get_recent_trade_asset_log(limit=50)
        market_round_stats = db.get_recent_market_round_stats(limit=24)
        policy_history = db.get_recent_policy_events(limit=12)
        fundamental_events = db.get_recent_fundamental_events(limit=16)
        bulletins = db.get_recent_bulletins(limit=20)
        health_report = db.run_health_check(prices=prices)
        daily_audits = db.get_recent_daily_audits(limit=8)
        forum_posts = db.get_recent_forum_posts(limit=120)

        agent_details = {}
        for agent_id in agent_ids:
            snapshot = db.get_agent_snapshot(agent_id)
            recent_trades = db.get_recent_trade_asset_log(agent_id=agent_id, limit=8)
            agent_details[agent_id] = {
                "profile": snapshot,
                "recent_trades": recent_trades,
                "asset_history": asset_history.get(agent_id, []),
            }

        candles = {}
        intraday_candles = {}
        for symbol in prices.keys():
            history = db.get_market_history(symbol, limit=240)
            candles[symbol] = build_daily_candles(history)
            intraday_candles[symbol] = build_intraday_candles(history)

        submitted_orders = extract_submitted_orders_from_log(SIMULATION_STDERR_PATH)
        matched_trades = build_matched_trades(trade_asset_log)
        stderr_tail = get_last_lines(SIMULATION_STDERR_PATH, num_lines=20)
        simulation_status = read_json_file(
            STATUS_PATH,
            {
                "status": "unknown",
                "message": "No simulation status file yet",
                "phase": "UNKNOWN",
                "day": 0,
                "tick": 0,
                "day_tick": 0,
                "virtual_time": "Unavailable",
            },
        )

        return jsonify(
            {
                "simulation_status": simulation_status,
                "prices": prices,
                "stock_catalog": stock_catalog,
                "wealth": wealth,
                "submitted_orders": submitted_orders,
                "matched_trades": matched_trades,
                "stderr_tail": stderr_tail,
                "agent_ids": agent_ids,
                "asset_history": asset_history,
                "agent_details": agent_details,
                "trade_asset_log": trade_asset_log,
                "market_round_stats": market_round_stats,
                "policy_history": policy_history,
                "fundamental_events": fundamental_events,
                "bulletins": bulletins,
                "health_report": health_report,
                "daily_audits": daily_audits,
                "forum_posts": forum_posts,
                "daily_candles": candles,
                "intraday_candles": intraday_candles,
            }
        )
    except Exception:
        logger.exception("/api/data failed")
        return internal_error_response()


@app.route("/api/create-agent", methods=["POST"])
def create_agent():
    if db is None:
        return jsonify({"error": "Database connection not established"}), 500

    try:
        payload = request.get_json(force=True) or {}
        display_name = str(payload.get("display_name", "")).strip()
        role_name = str(payload.get("role_name", "")).strip() or "Custom Entrant"
        base_prompt = str(payload.get("base_prompt", "")).strip()
        if not display_name:
            return jsonify({"error": "Display name is required."}), 400
        if not base_prompt:
            return jsonify({"error": "Prompt / traits is required."}), 400

        starting_cash = max(0.0, float(payload.get("starting_cash", 10000)))
        salary = max(0.0, float(payload.get("salary", 500)))
        cost_multiplier = max(0.1, float(payload.get("cost_multiplier", 1.0)))
        raw_holdings = payload.get("holdings", {}) or {}
        prices = db.get_all_stock_prices()
        holdings = {}
        for symbol, quantity in raw_holdings.items():
            if symbol not in prices:
                continue
            qty = max(0, int(quantity or 0))
            if qty > 0:
                holdings[symbol] = qty

        agent_id = build_agent_id(display_name)
        write_agent_config(agent_id, role_name, starting_cash, salary, cost_multiplier, base_prompt)
        db.create_agent(
            agent_id=agent_id,
            role=role_name,
            cash=starting_cash,
            salary=salary,
            cost_multiplier=cost_multiplier,
            holdings=holdings,
        )

        runtime = db.get_runtime_state()
        stock_value = sum(float(prices.get(symbol, 0.0)) * float(quantity) for symbol, quantity in holdings.items())
        total_assets = round(float(starting_cash) + stock_value, 2)
        db.record_asset_snapshot(
            agent_id=agent_id,
            tick=int(runtime.get("current_tick", 0)),
            day=int(runtime.get("day_count", 1)),
            day_tick=int(runtime.get("day_tick", 0)),
            phase=str(runtime.get("phase", "CLOSED")),
            source="agent_creation",
            cash=round(float(starting_cash), 2),
            stock_value=round(stock_value, 2),
            total_assets=total_assets,
            holdings=holdings,
            note="Created from web form",
        )

        bundle_text = ", ".join(f"{symbol}:{qty}" for symbol, qty in holdings.items()) or "no starting stocks"
        forum = VirtualForum(db)
        forum.add_post(
            "MarketDesk",
            f"{agent_id} joined the market as {role_name} with ${starting_cash:,.2f} cash and {bundle_text}.",
            sentiment=0.25,
            is_hot=bool(holdings),
        )

        return jsonify(
            {
                "ok": True,
                "agent_id": agent_id,
                "role": role_name,
                "cash": round(float(starting_cash), 2),
                "holdings": holdings,
                "stock_value": round(stock_value, 2),
                "total_assets": total_assets,
            }
        )
    except ValueError as exc:
        return jsonify({"error": f"Invalid numeric input: {exc}"}), 400
    except Exception:
        logger.exception("Request failed")
        return internal_error_response()


@app.route("/api/post-bulletin", methods=["POST"])
def post_bulletin():
    if db is None:
        return jsonify({"error": "Database connection not established"}), 500

    try:
        payload = request.get_json(force=True) or {}
        title = str(payload.get("title", "")).strip()
        content = str(payload.get("content", "")).strip()
        importance = round(max(0.0, min(1.0, float(payload.get("importance", 0.75) or 0.75))), 2)

        if not title:
            return jsonify({"error": "Bulletin title is required."}), 400
        if not content:
            return jsonify({"error": "Bulletin content is required."}), 400

        runtime = db.get_runtime_state()
        bulletin_id = db.record_bulletin(
            day=int(runtime.get("day_count", 1)),
            tick=int(runtime.get("current_tick", 0)),
            day_tick=int(runtime.get("day_tick", 0)),
            phase=str(runtime.get("phase", "UNKNOWN")),
            title=title,
            content=content,
            importance=importance,
            author_id="BulletinBoard",
            expires_at=None,
        )
        bulletin_entry = {
            "bulletin_id": bulletin_id,
            "day": int(runtime.get("day_count", 1)),
            "tick": int(runtime.get("current_tick", 0)),
            "day_tick": int(runtime.get("day_tick", 0)),
            "phase": str(runtime.get("phase", "UNKNOWN")),
            "title": title,
            "summary": content,
            "importance": importance,
            "retain": True,
            "source": "manual_bulletin",
        }
        append_bulletin_memory_to_all_agents(bulletin_entry)

        forum = VirtualForum(db)
        forum.add_post(
            "BulletinBoard",
            f"[{title}] {content}",
            sentiment=0.0,
            is_hot=True,
        )

        return jsonify({"ok": True, "bulletin_id": bulletin_id, "title": title, "importance": importance})
    except ValueError as exc:
        return jsonify({"error": f"Invalid numeric input: {exc}"}), 400
    except Exception:
        logger.exception("Request failed")
        return internal_error_response()


@app.route("/api/agent/<agent_id>/disable", methods=["POST"])
def disable_agent(agent_id: str):
    if db is None:
        return jsonify({"error": "Database connection not established"}), 500
    if not AGENT_ID_PATTERN.match(agent_id or ""):
        return jsonify({"error": "Invalid agent id."}), 400

    try:
        snapshot = db.get_agent_snapshot(agent_id)
        if not snapshot:
            return jsonify({"error": "Agent not found."}), 404
        if not snapshot.get("is_active", True):
            return jsonify({"ok": True, "agent_id": agent_id, "status": "already_disabled"})
        db.set_agent_active(agent_id, False)
        return jsonify({"ok": True, "agent_id": agent_id, "status": "disabled"})
    except Exception:
        logger.exception("Request failed")
        return internal_error_response()


@app.route("/api/agent/<agent_id>/delete", methods=["POST"])
def delete_agent(agent_id: str):
    if db is None:
        return jsonify({"error": "Database connection not established"}), 500
    if not AGENT_ID_PATTERN.match(agent_id or ""):
        return jsonify({"error": "Invalid agent id."}), 400

    try:
        snapshot = db.get_agent_snapshot(agent_id)
        if not snapshot:
            return jsonify({"error": "Agent not found."}), 404
        deleted = db.delete_agent(agent_id)
        remove_agent_config(agent_id)
        return jsonify({"ok": deleted, "agent_id": agent_id, "status": "deleted" if deleted else "not_found"})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        logger.exception("Failed to delete agent")
        return internal_error_response()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if WEB_MONITOR_HOST not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "Binding to %s exposes the dashboard beyond this machine; "
            "set DASHBOARD_TOKEN and put it behind a reverse proxy with authentication.",
            WEB_MONITOR_HOST,
        )
    app.run(host=WEB_MONITOR_HOST, port=5000, debug=WEB_MONITOR_DEBUG)
