import json
import logging
import os
import random
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, time as dt_time
from typing import Any, Dict, List, Optional, Tuple

from database import SimulationDB
from forum_system import VirtualForum
from llm_router import AgentBrain
from main_loop import MarketState, VirtualClock
from market_engine import MarketEngine
from stock_split_service import apply_stock_split

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("SimulationServer")

COMPANY_PROFILES = {
    "VDA": {
        "name": "Vision Dynamics AI",
        "business": "Builds AI infrastructure and platform services for the simulated market.",
        "vision": "Become the core compute layer for every ambitious AI company in the world.",
    },
    "ITE": {
        "name": "IdeaTech Entertainment",
        "business": "Mixes consumer internet products with high-growth AI applications.",
        "vision": "Win attention first, then convert cultural relevance into massive platform value.",
    },
    "SLA": {
        "name": "Sola Mobility",
        "business": "Combines energy, transport, and robotics into one vertically integrated story.",
        "vision": "Own the physical-world AI stack from battery to autonomy.",
    },
    "SMC": {
        "name": "Smart Manufacturing Corp",
        "business": "Provides chips, manufacturing systems, and industrial supply chain capacity.",
        "vision": "Become the dependable backbone behind every AI hardware winner.",
    },
}

STOCK_FUNDAMENTAL_MODELS = {
    "VDA": {
        "shares_outstanding": 120_000_000.0,
        "forward_eps": 3.2,
        "growth_rate": 0.18,
        "base_pe": 30.0,
        "pe_volatility": 3.5,
        "dividend_payout_ratio": 0.10,
        "liquidation_anchor": 0.42,
        "risk_score": 0.58,
        "sentiment_beta": 1.20,
    },
    "ITE": {
        "shares_outstanding": 180_000_000.0,
        "forward_eps": 1.6,
        "growth_rate": 0.10,
        "base_pe": 22.0,
        "pe_volatility": 4.5,
        "dividend_payout_ratio": 0.16,
        "liquidation_anchor": 0.48,
        "risk_score": 0.66,
        "sentiment_beta": 1.35,
    },
    "SLA": {
        "shares_outstanding": 95_000_000.0,
        "forward_eps": 2.0,
        "growth_rate": 0.24,
        "base_pe": 36.0,
        "pe_volatility": 7.0,
        "dividend_payout_ratio": 0.02,
        "liquidation_anchor": 0.26,
        "risk_score": 0.82,
        "sentiment_beta": 1.65,
    },
    "SMC": {
        "shares_outstanding": 240_000_000.0,
        "forward_eps": 4.1,
        "growth_rate": 0.05,
        "base_pe": 14.0,
        "pe_volatility": 2.0,
        "dividend_payout_ratio": 0.55,
        "liquidation_anchor": 0.72,
        "risk_score": 0.34,
        "sentiment_beta": 0.82,
    },
}

FUNDAMENTAL_EVENT_TEMPLATES = {
    "earnings_beat": {
        "title": "Quarterly earnings beat expectations",
        "impact": 0.65,
    },
    "earnings_miss": {
        "title": "Quarterly earnings missed expectations",
        "impact": -0.70,
    },
    "guidance_raise": {
        "title": "Management raised forward guidance",
        "impact": 0.55,
    },
    "guidance_cut": {
        "title": "Management cut forward guidance",
        "impact": -0.60,
    },
    "dividend_raise": {
        "title": "Board approved a dividend increase",
        "impact": 0.35,
    },
    "dividend_cut": {
        "title": "Board approved a dividend cut",
        "impact": -0.45,
    },
}


def resolve_llm_provider(api_key: str) -> str:
    """Pick the LLM backend.

    Explicit ``LLM_PROVIDER`` always wins; otherwise the provider is inferred
    from the key/environment so existing MIMO / Gemini setups keep working
    unchanged. Supported values: auto, xiaomi_openai, gemini, llama_cpp.
    """
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if str(api_key).startswith("tp-") or os.environ.get("MIMO_API_KEY"):
        return "xiaomi_openai"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("LLAMA_BASE_URL"):
        return "llama_cpp"
    return "auto"


class SimulationServer:
    def __init__(self, api_key: str, sleep_interval: int = 300):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.db_path = os.path.join(self.base_dir, "server_society.db")
        self.agents_dir = os.path.join(self.base_dir, "agents")
        self.trade_log_path = os.path.join(self.base_dir, "trade_log.md")
        self.status_path = os.path.join(self.base_dir, "simulation_status.json")
        self.daily_audit_log_path = os.path.join(self.base_dir, "daily_audit_log.md")

        self.db = SimulationDB(self.db_path)
        self.clock = VirtualClock()
        # Model/base URL defaults live in llm_router.PROVIDER_DEFAULTS and are
        # overridable per provider through MIMO_* / LLAMA_* / LLM_TIMEOUT_* env vars.
        self.brain = AgentBrain(api_key=api_key, provider=resolve_llm_provider(api_key))
        if self.brain.provider == "gemini" and not self.brain.api_key:
            logger.warning(
                "No API key configured. Set MIMO_API_KEY or GEMINI_API_KEY, "
                "or run a local llama.cpp server with LLM_PROVIDER=llama_cpp."
            )
        self.forum = VirtualForum(self.db)
        self.engine = MarketEngine()
        self.sleep_interval = sleep_interval
        self.base_living_cost = 400.0
        self.active_policy: Dict[str, Any] = {
            "policy_type": None,
            "title": None,
            "summary": None,
            "day": None,
            "issued_at": None,
            "expires_at": None,
        }
        self.agent_traits: Dict[str, str] = {}
        self.policy_config = {
            "living_cost_min": 250.0,
            "living_cost_max": 550.0,
            "living_cost_step": 150.0,
            "living_cost_baseline": 400.0,
            "living_cost_tighten": 550.0,
            "living_cost_relief": 250.0,
            "poverty_threshold": 10000.0,
            "rich_threshold": 100000.0,
            "wealth_tax_rate": 0.02,
            "poverty_rate_trigger": 0.40,
            "cash_stress_trigger": 0.50,
            "avg_return_trigger": 0.15,
            "concentration_trigger": 0.45,
        }
        self.stock_split_config = {
            "enabled": True,
            "trigger_price": 500.0,
            "default_ratio": 2,
        }
        self.fundamental_event_config = {
            "enabled": True,
            "impact_scale": 1.0,
            "probabilities": {
                "earnings_beat": 0.12,
                "earnings_miss": 0.10,
                "guidance_raise": 0.07,
                "guidance_cut": 0.07,
                "dividend_raise": 0.05,
                "dividend_cut": 0.04,
            },
            "ranges": {
                "earnings_beat": {
                    "eps_multiplier": (1.06, 1.16),
                    "growth_delta": (0.01, 0.04),
                    "pe_delta": (0.8, 2.5),
                    "dividend_multiplier": (1.0, 1.0),
                },
                "earnings_miss": {
                    "eps_multiplier": (0.84, 0.96),
                    "growth_delta": (-0.05, -0.015),
                    "pe_delta": (-2.8, -0.9),
                    "dividend_multiplier": (1.0, 1.0),
                },
                "guidance_raise": {
                    "eps_multiplier": (1.03, 1.10),
                    "growth_delta": (0.015, 0.05),
                    "pe_delta": (0.5, 1.8),
                    "dividend_multiplier": (1.0, 1.0),
                },
                "guidance_cut": {
                    "eps_multiplier": (0.88, 0.98),
                    "growth_delta": (-0.06, -0.02),
                    "pe_delta": (-2.2, -0.7),
                    "dividend_multiplier": (1.0, 1.0),
                },
                "dividend_raise": {
                    "eps_multiplier": (1.0, 1.0),
                    "growth_delta": (0.0, 0.0),
                    "pe_delta": (0.2, 1.0),
                    "dividend_multiplier": (1.08, 1.22),
                },
                "dividend_cut": {
                    "eps_multiplier": (1.0, 1.0),
                    "growth_delta": (0.0, 0.0),
                    "pe_delta": (-1.5, -0.3),
                    "dividend_multiplier": (0.70, 0.92),
                },
            },
        }
        self.fast_burn_mode = True
        self.price_history_window = 30
        self.llm_request_pause_seconds = 0.0
        self.round_sleep_seconds_open = 1
        self.round_sleep_seconds_closed = 1
        self.llm_max_concurrency = 4
        self.llm_target_rpm = 80
        self._rpm_window_seconds = 60.0
        self._request_timestamps: deque[float] = deque()
        self._request_lock = threading.Lock()
        self._latest_round_metrics: Dict[str, Any] = {
            "total_jobs": 0,
            "successful_responses": 0,
            "fallback_count": 0,
            "valid_orders": 0,
            "matched_trades": 0,
        }

        self._load_agent_traits()
        self._restore_runtime_state()
        self._write_status(status="booting", message="Simulation server initialized")

    def _current_phase_name(self) -> str:
        return "OPEN" if self.clock.state == MarketState.OPEN else "CLOSED"

    def _write_status(self, status: str, message: str, extra: Dict[str, Any] | None = None):
        payload = {
            "status": status,
            "message": message,
            "day": self.clock.day_count,
            "tick": self.clock.current_tick,
            "day_tick": self.clock.day_tick,
            "phase": self._current_phase_name(),
            "virtual_time": self.clock.get_virtual_time(),
            "base_living_cost": round(self.base_living_cost, 2),
            "fundamental_event_config": self.fundamental_event_config,
            "active_policy_type": self.active_policy.get("policy_type"),
            "active_policy_summary": self.active_policy.get("summary"),
            "active_policy_day": self.active_policy.get("day"),
            "active_policy_expires_at": self.active_policy.get("expires_at"),
            "updated_at": datetime.now().isoformat(),
            "round_metrics": self._latest_round_metrics,
        }
        if extra:
            payload.update(extra)
        # Write-then-rename: the dashboard reads this file from another process
        # and must never observe a half-written JSON document.
        temp_path = f"{self.status_path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, self.status_path)

    def _load_runtime_hint_from_status(self) -> Dict[str, Any] | None:
        if not os.path.exists(self.status_path):
            return None
        try:
            with open(self.status_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

        if payload.get("tick", 0) <= 0 and payload.get("day", 1) <= 1 and payload.get("day_tick", 0) <= 0:
            return None
        return payload

    def _restore_runtime_state(self):
        runtime_state = self.db.get_runtime_state()
        if (
            runtime_state.get("current_tick", 0) == 0
            and runtime_state.get("day_count", 1) == 1
            and runtime_state.get("day_tick", 0) == 0
        ):
            status_hint = self._load_runtime_hint_from_status()
            if status_hint:
                runtime_state = {
                    "current_tick": status_hint.get("tick", 0),
                    "day_tick": status_hint.get("day_tick", 0),
                    "day_count": status_hint.get("day", 1),
                    "phase": status_hint.get("phase", "CLOSED"),
                }
            else:
                activity_hint = self.db.get_latest_activity_state()
                if activity_hint:
                    runtime_state = {
                        "current_tick": activity_hint.get("tick", 0),
                        "day_tick": activity_hint.get("day_tick", 0),
                        "day_count": activity_hint.get("day", 1),
                        "phase": activity_hint.get("phase", "CLOSED"),
                    }
        self.clock.restore(
            current_tick=runtime_state.get("current_tick", 0),
            day_tick=runtime_state.get("day_tick", 0),
            day_count=runtime_state.get("day_count", 1),
            phase=runtime_state.get("phase"),
        )
        self.base_living_cost = float(runtime_state.get("base_living_cost", self.base_living_cost))
        self.active_policy = {
            "policy_type": runtime_state.get("active_policy_type"),
            "title": runtime_state.get("active_policy_title"),
            "summary": runtime_state.get("active_policy_summary"),
            "day": runtime_state.get("active_policy_day"),
            "issued_at": runtime_state.get("active_policy_issued_at"),
            "expires_at": runtime_state.get("active_policy_expires_at"),
        }
        self._save_runtime_state()
        logger.info(
            "Runtime restored: day=%s tick=%s day_tick=%s phase=%s living_cost=%s",
            self.clock.day_count,
            self.clock.current_tick,
            self.clock.day_tick,
            self._current_phase_name(),
            self.base_living_cost,
        )

    def _save_runtime_state(self):
        self.db.save_runtime_state(
            current_tick=self.clock.current_tick,
            day_tick=self.clock.day_tick,
            day_count=self.clock.day_count,
            phase=self._current_phase_name(),
            base_living_cost=self.base_living_cost,
            active_policy_type=self.active_policy.get("policy_type"),
            active_policy_title=self.active_policy.get("title"),
            active_policy_summary=self.active_policy.get("summary"),
            active_policy_day=self.active_policy.get("day"),
            active_policy_issued_at=self.active_policy.get("issued_at"),
            active_policy_expires_at=self.active_policy.get("expires_at"),
        )

    def _load_agent_traits(self):
        if not os.path.exists(self.agents_dir):
            return

        for filename in os.listdir(self.agents_dir):
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.agents_dir, filename), "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                agent_id = cfg["id"]
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                logger.warning("Skipping unreadable agent config: %s", filename)
                continue
            if not isinstance(agent_id, str) or not agent_id:
                continue
            traits = cfg.get("traits", "Standard investor")
            self.agent_traits[agent_id] = traits if isinstance(traits, str) else str(traits)

    def _normalize_trade_amount(self, value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _minimum_cash_reserve(self, state: Dict[str, Any]) -> float:
        cost_multiplier = self._as_float(state.get("cost_multiplier"), 1.0)
        # Agents must keep more than one full day of living cost in cash.
        living_cost_reserve = (self.base_living_cost * cost_multiplier) + 0.01

        holdings = state.get("holdings", {})
        if not isinstance(holdings, dict):
            holdings = self._as_dict(self._get_json_field(state, "holdings", {}))
        total_assets = self._compute_stock_value(holdings) + self._as_float(state.get("cash"), 0.0)

        # Wealthy agents must also keep a strategic cash buffer so they cannot
        # hide from day-close policy effects by going fully invested.
        rich_threshold = float(self.policy_config.get("rich_threshold", 100000.0))
        rich_cash_buffer_ratio = 0.10
        rich_cash_buffer = total_assets * rich_cash_buffer_ratio if total_assets >= rich_threshold else 0.0

        return round(max(living_cost_reserve, rich_cash_buffer), 2)

    def _get_json_field(self, state: Dict[str, Any], key: str, default: Any):
        value = state.get(key, default)
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return default
        return value

    def _compute_stock_value(self, holdings: Dict[str, Any], prices: Dict[str, float] | None = None) -> float:
        if not isinstance(holdings, dict):
            return 0.0
        prices = prices or self.db.get_all_stock_prices()
        total = 0.0
        for symbol, quantity in holdings.items():
            total += self._as_float(prices.get(symbol, 0.0), 0.0) * self._as_float(quantity, 0.0)
        return round(total, 2)

    def _compute_agent_assets(self, agent_state: Dict[str, Any], prices: Dict[str, float] | None = None) -> Dict[str, Any]:
        cash = round(self._as_float(agent_state.get("cash"), 0.0), 2)
        holdings = self._as_dict(agent_state.get("holdings"))
        stock_value = self._compute_stock_value(holdings, prices)
        return {
            "cash": cash,
            "stock_value": stock_value,
            "total_assets": round(cash + stock_value, 2),
            "holdings": holdings,
        }

    def _trim_memory(self, values: List[Dict[str, Any]], limit: int = 20) -> List[Dict[str, Any]]:
        return values[-limit:]

    def _wait_for_rpm_slot(self):
        while True:
            wait_seconds = 0.0
            now = time.time()
            with self._request_lock:
                while self._request_timestamps and (now - self._request_timestamps[0]) >= self._rpm_window_seconds:
                    self._request_timestamps.popleft()

                if len(self._request_timestamps) < self.llm_target_rpm:
                    self._request_timestamps.append(now)
                    return

                oldest = self._request_timestamps[0]
                wait_seconds = max(0.05, self._rpm_window_seconds - (now - oldest))

            time.sleep(wait_seconds)

    def _throttled_think(self, agent_id: str, role: str, prompt: str, mode: str) -> Dict[str, Any]:
        self._wait_for_rpm_slot()
        return self.brain.think(agent_id, role, prompt, mode=mode)

    def _run_llm_jobs(self, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not jobs:
            return []

        max_workers = max(1, min(self.llm_max_concurrency, len(jobs)))
        results: List[Dict[str, Any] | None] = [None] * len(jobs)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    self._throttled_think,
                    job["agent_id"],
                    job["role"],
                    job["prompt"],
                    job["mode"],
                ): index
                for index, job in enumerate(jobs)
            }
            for future in as_completed(future_map):
                index = future_map[future]
                try:
                    results[index] = future.result()
                except Exception as exc:
                    job = jobs[index]
                    logger.warning(
                        "Concurrent think failed for %s (%s): %s",
                        job["agent_id"],
                        job["mode"],
                        exc,
                    )
                    results[index] = self._fallback_decision(job["mode"])

        # The result list must stay index-aligned with `jobs`: callers zip it
        # against the job metadata, so dropping entries here would silently
        # attach one agent's decision to another agent.
        return [
            result if isinstance(result, dict) else self._fallback_decision(job["mode"])
            for result, job in zip(results, jobs)
        ]

    def _fallback_decision(self, mode: str) -> Dict[str, Any]:
        """Same fallback shapes as llm_router.MODE_CONFIGS (single source: llm_router)."""
        from llm_router import MODE_CONFIGS

        config = MODE_CONFIGS.get(mode) or {}
        fallback = dict(config.get("fallback") or {})
        if mode == "reflection":
            fallback.setdefault("belief_memory", {})
            fallback.setdefault("episodic_summary", {})
            fallback.setdefault("relationship_memory", {})
            fallback.setdefault("bulletin_memory", [])
            fallback.setdefault("fundamental_memory", [])
        if mode == "market":
            fallback.setdefault("trades", [])
        fallback.setdefault("thought_process", "System fallback.")
        return fallback

    def _is_fallback_decision(self, decision: Dict[str, Any]) -> bool:
        thought = str(decision.get("thought_process") or decision.get("reflection_summary") or "").lower()
        return "fallback" in thought

    def _set_round_metrics(
        self,
        *,
        total_jobs: int,
        successful_responses: int,
        fallback_count: int,
        valid_orders: int,
        matched_trades: int,
    ):
        self._latest_round_metrics = {
            "total_jobs": int(total_jobs),
            "successful_responses": int(successful_responses),
            "fallback_count": int(fallback_count),
            "valid_orders": int(valid_orders),
            "matched_trades": int(matched_trades),
        }

    def _memory_digest(self, state: Dict[str, Any]) -> Dict[str, Any]:
        active_day = self.active_policy.get("day")
        policy_memory = self._get_json_field(state, "policy_memory", [])
        bulletin_memory = self._get_json_field(state, "bulletin_memory", [])
        fundamental_memory = self._get_json_field(state, "fundamental_memory", [])
        if self._is_policy_notice_active() and active_day is not None:
            policy_memory = [item for item in policy_memory if item.get("day") in {active_day, active_day - 1}]
        else:
            policy_memory = []
        return {
            "working_trade_memory": self._get_json_field(state, "trading_memory", [])[-5:],
            "working_social_memory": self._get_json_field(state, "social_memory", [])[-5:],
            "policy_memory": policy_memory[-2:],
            "bulletin_memory": bulletin_memory[-3:],
            "fundamental_memory": fundamental_memory[-4:],
            "episodic_summary": self._get_json_field(state, "episodic_summary", {}),
            "belief_memory": self._get_json_field(state, "belief_memory", {}),
            "relationship_memory": self._get_json_field(state, "relationship_memory", {}),
        }

    def _build_memory_context(self, state: Dict[str, Any], mode: str) -> str:
        memory = self._memory_digest(state)
        sections = [
            f"Your Working Trade Memory: {memory['working_trade_memory'] or 'None'}",
            f"Your Working Social Memory: {memory['working_social_memory'] or 'None'}",
            f"Your Policy Memory: {memory['policy_memory'] or 'None'}",
            f"Your Bulletin Memory: {memory['bulletin_memory'] or 'None'}",
            f"Your Fundamental Event Memory: {memory['fundamental_memory'] or 'None'}",
            f"Your Episodic Summary: {memory['episodic_summary'] or 'None'}",
            f"Your Core Beliefs: {memory['belief_memory'] or 'None'}",
            f"Your Relationship Memory: {memory['relationship_memory'] or 'None'}",
        ]

        if mode == "social":
            sections = [
                f"Your Working Social Memory: {memory['working_social_memory'] or 'None'}",
                f"Your Policy Memory: {memory['policy_memory'] or 'None'}",
                f"Your Bulletin Memory: {memory['bulletin_memory'] or 'None'}",
                f"Your Fundamental Event Memory: {memory['fundamental_memory'] or 'None'}",
                f"Your Episodic Summary: {memory['episodic_summary'] or 'None'}",
                f"Your Core Beliefs: {memory['belief_memory'] or 'None'}",
                f"Your Relationship Memory: {memory['relationship_memory'] or 'None'}",
            ]

        return "\n".join(sections) + "\n"

    def _stringify_memory_value(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "; ".join(str(item) for item in value if item)
        if isinstance(value, dict):
            return "; ".join(f"{key}: {val}" for key, val in value.items() if val)
        return str(value)

    def _truncate_text(self, value: Any, limit: int = 140) -> str:
        text = self._stringify_memory_value(value).strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

    def _compact_key_value_memory(self, mapping: Dict[str, Any], limit: int = 3, value_limit: int = 90) -> Dict[str, str]:
        compact: Dict[str, str] = {}
        if not isinstance(mapping, dict):
            return compact
        for key, value in list(mapping.items())[:limit]:
            clean_key = str(key).strip()
            clean_value = self._truncate_text(value, value_limit)
            if clean_key and clean_value:
                compact[clean_key] = clean_value
        return compact

    def _compact_reflection_records(self, items: List[Dict[str, Any]], limit: int = 3) -> List[Dict[str, Any]]:
        compact: List[Dict[str, Any]] = []
        for item in items[-limit:]:
            if not isinstance(item, dict):
                continue
            record: Dict[str, Any] = {}
            for key in ("tick", "stock", "action", "amount", "price", "decision", "target_id", "stance", "event_type", "symbol"):
                if key in item and item.get(key) not in (None, "", []):
                    record[key] = item.get(key)
            if "title" in item and item.get("title"):
                record["title"] = self._truncate_text(item.get("title"), 72)
            summary_text = (
                item.get("summary")
                or item.get("feed_summary")
                or item.get("content")
                or item.get("thought")
                or item.get("impact_hint")
            )
            if summary_text:
                record["summary"] = self._truncate_text(summary_text, 120)
            if "importance" in item:
                try:
                    record["importance"] = round(float(item.get("importance", 0.0)), 2)
                except Exception:
                    pass
            if "trust" in item:
                try:
                    record["trust"] = round(float(item.get("trust", 0.0)), 2)
                except Exception:
                    pass
            if "retain" in item:
                record["retain"] = bool(item.get("retain"))
            if record:
                compact.append(record)
        return compact

    def _compact_episodic_summary(self, summary: Dict[str, Any]) -> Dict[str, str]:
        if not isinstance(summary, dict):
            return {}
        return {
            "recent_market_story": self._truncate_text(summary.get("recent_market_story", ""), 110),
            "recent_behavior_pattern": self._truncate_text(summary.get("recent_behavior_pattern", ""), 110),
            "recent_lesson": self._truncate_text(summary.get("recent_lesson", ""), 110),
        }

    def _compress_episodic_summary(
        self,
        current_summary: Dict[str, Any],
        proposed_summary: Dict[str, Any],
    ) -> Dict[str, str]:
        current_summary = current_summary or {}
        proposed_summary = proposed_summary or {}

        previous_day = current_summary.get("last_reflection_day")
        prior_story = self._stringify_memory_value(current_summary.get("recent_market_story", "")).strip()
        prior_behavior = self._stringify_memory_value(current_summary.get("recent_behavior_pattern", "")).strip()
        prior_lesson = self._stringify_memory_value(current_summary.get("recent_lesson", "")).strip()

        rolling_lines = [
            line.strip()
            for line in self._stringify_memory_value(current_summary.get("rolling_context", "")).split("\n")
            if line.strip()
        ]
        if any([prior_story, prior_behavior, prior_lesson]):
            day_label = previous_day if previous_day not in (None, "") else "?"
            compact_line = (
                f"Day {day_label}: market={prior_story or 'n/a'} | "
                f"behavior={prior_behavior or 'n/a'} | lesson={prior_lesson or 'n/a'}"
            )
            if not rolling_lines or rolling_lines[-1] != compact_line:
                rolling_lines.append(compact_line)
        rolling_lines = rolling_lines[-3:]

        takeaway_candidates = []
        existing_takeaways = self._stringify_memory_value(current_summary.get("long_horizon_takeaways", ""))
        takeaway_candidates.extend([item.strip() for item in existing_takeaways.split(";") if item.strip()])
        if prior_lesson:
            takeaway_candidates.append(prior_lesson)
        new_lesson = self._stringify_memory_value(proposed_summary.get("recent_lesson", prior_lesson)).strip()
        if new_lesson:
            takeaway_candidates.append(new_lesson)

        deduped_takeaways: List[str] = []
        seen_takeaways = set()
        for item in takeaway_candidates:
            marker = item.lower()
            if marker in seen_takeaways:
                continue
            seen_takeaways.add(marker)
            deduped_takeaways.append(item)
        deduped_takeaways = deduped_takeaways[-3:]

        return {
            "recent_market_story": self._stringify_memory_value(
                proposed_summary.get("recent_market_story", current_summary.get("recent_market_story", ""))
            ),
            "recent_behavior_pattern": self._stringify_memory_value(
                proposed_summary.get("recent_behavior_pattern", current_summary.get("recent_behavior_pattern", ""))
            ),
            "recent_lesson": new_lesson or prior_lesson,
            "rolling_context": "\n".join(rolling_lines),
            "long_horizon_takeaways": "; ".join(deduped_takeaways),
            "last_reflection_day": str(self.clock.day_count),
        }

    def _stabilize_belief_memory(
        self,
        current_beliefs: Dict[str, Any],
        proposed_beliefs: Dict[str, Any],
        trading_memory: List[Dict[str, Any]],
        social_memory: List[Dict[str, Any]],
        policy_memory: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        current_beliefs = current_beliefs or {}
        proposed_beliefs = proposed_beliefs or {}
        stabilized = {str(key): self._stringify_memory_value(value) for key, value in current_beliefs.items() if value}

        trade_counts: Dict[str, int] = {}
        trading_memory = [item for item in (trading_memory or []) if isinstance(item, dict)]
        social_memory = [item for item in (social_memory or []) if isinstance(item, dict)]
        policy_memory = [item for item in (policy_memory or []) if isinstance(item, dict)]

        for item in trading_memory[-8:]:
            stock = str(item.get("stock", "")).strip().upper()
            if stock:
                trade_counts[stock] = trade_counts.get(stock, 0) + 1

        social_text = " ".join(
            f"{item.get('feed_summary', '')} {item.get('content', '')} {item.get('thought', '')}".lower()
            for item in social_memory[-8:]
        )
        policy_text = " ".join(
            f"{item.get('summary', '')} {item.get('impact_hint', '')}".lower()
            for item in policy_memory[-4:]
        )
        personally_taxed = any(
            isinstance(item.get("personal_impact"), dict) and item["personal_impact"].get("taxed")
            for item in policy_memory[-4:]
        )

        macro_belief_keys = {"market", "risk", "liquidity", "cash", "policy", "diversification", "self"}

        for raw_key, raw_value in proposed_beliefs.items():
            key = str(raw_key).strip()
            value = self._stringify_memory_value(raw_value).strip()
            if not key or not value:
                continue

            evidence_score = 0
            upper_key = key.upper()
            lower_key = key.lower()

            if upper_key in trade_counts:
                evidence_score += 2 if trade_counts[upper_key] >= 2 else 1
            if lower_key in social_text:
                evidence_score += 1
            if lower_key in policy_text:
                evidence_score += 1
            if key in stabilized:
                evidence_score += 2
            if lower_key in macro_belief_keys:
                evidence_score += 1
            if personally_taxed and lower_key in {"policy", "cash", "risk", "liquidity", "market"}:
                evidence_score += 1

            if evidence_score >= 2:
                stabilized[key] = value

        return stabilized

    def _record_social_memory(self, agent_id: str, feed_text: str, decision: Dict[str, Any]):
        # Read and write inside one connection: reading through a second
        # connection would let a concurrent dashboard write get overwritten.
        with self.db._get_connection() as conn:
            row = conn.execute("SELECT social_memory FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if not row:
                return
            memory = self._as_list(self._get_json_field(dict(row), "social_memory", []))
            memory.append(
                {
                    "tick": self.clock.current_tick,
                    "feed_summary": feed_text[:240] + ("..." if len(feed_text) > 240 else ""),
                    "decision": decision.get("action"),
                    "content": self._safe_post_content(decision.get("content")),
                    "target_id": self._safe_parent_id(decision.get("target_id")),
                    "thought": str(decision.get("thought_process") or ""),
                    "timestamp": datetime.now().isoformat(),
                }
            )
            memory = self._trim_memory(memory, limit=20)
            conn.execute("UPDATE agents SET social_memory = ? WHERE id = ?", (json.dumps(memory, ensure_ascii=False), agent_id))

    def _should_mark_hot_post(self, agent_id: str, role: str, content: str, sentiment: float, action: str) -> bool:
        if action != "post":
            return False

        text = (content or "").strip()
        if not text:
            return False

        lower_text = text.lower()
        strong_keywords = [
            "breaking",
            "exclusive",
            "all-in",
            "bankruptcy",
            "lawsuit",
            "moon",
            "crash",
            "panic",
            "parabolic",
            "massive",
            "urgent",
        ]
        keyword_hits = sum(1 for word in strong_keywords if word in lower_text)
        emphasis_hits = text.count("!") + text.count("?")
        role_bonus = 1 if any(token in role.lower() for token in ["news", "boss", "whale", "gambler"]) else 0

        hot_score = 0
        if len(text) >= 140:
            hot_score += 1
        if abs(sentiment) >= 0.65:
            hot_score += 1
        if keyword_hits >= 1:
            hot_score += 1
        if emphasis_hits >= 2:
            hot_score += 1
        hot_score += role_bonus

        return hot_score >= 2

    def _latest_policy_notice(self) -> str:
        if not self._is_policy_notice_active() or not self.active_policy.get("summary"):
            return "No recent central policy notice."
        return f"{self.active_policy.get('title')}: {self.active_policy.get('summary')}"

    def _is_policy_notice_active(self) -> bool:
        if not self.active_policy.get("policy_type"):
            return False
        expires_at = self.active_policy.get("expires_at")
        if not expires_at:
            return False
        try:
            return datetime.now() < datetime.fromisoformat(expires_at)
        except ValueError:
            return False

    def _set_active_policy(
        self,
        policy_type: str | None,
        title: str | None,
        summary: str | None,
        day: int | None,
        issued_at: str | None = None,
        expires_at: str | None = None,
    ):
        self.active_policy = {
            "policy_type": policy_type,
            "title": title,
            "summary": summary,
            "day": day,
            "issued_at": issued_at,
            "expires_at": expires_at,
        }
        self._save_runtime_state()

    def _append_policy_memory_to_all_agents(
        self,
        policy_entry: Dict[str, Any],
        personal_impacts: Dict[str, Dict[str, Any]] | None = None,
    ):
        personal_impacts = personal_impacts or {}
        with self.db._get_connection() as conn:
            agents = conn.execute("SELECT id, policy_memory FROM agents WHERE is_active = 1").fetchall()
            for agent in agents:
                existing = self._get_json_field(dict(agent), "policy_memory", [])
                entry = dict(policy_entry)
                entry["personal_impact"] = personal_impacts.get(
                    agent["id"],
                    {
                        "taxed": False,
                        "tax_due": 0.0,
                        "actual_tax": 0.0,
                        "cash_before_tax": None,
                        "cash_after_tax": None,
                    },
                )
                existing.append(entry)
                existing = self._trim_memory(existing, limit=10)
                conn.execute(
                    "UPDATE agents SET policy_memory = ? WHERE id = ?",
                    (json.dumps(existing, ensure_ascii=False), agent["id"]),
                )

    def _append_bulletin_memory_to_all_agents(self, bulletin_entry: Dict[str, Any]):
        with self.db._get_connection() as conn:
            agents = conn.execute("SELECT id, bulletin_memory FROM agents WHERE is_active = 1").fetchall()
            for agent in agents:
                existing = self._get_json_field(dict(agent), "bulletin_memory", [])
                existing.append(dict(bulletin_entry))
                existing = self._trim_memory(existing, limit=8)
                conn.execute(
                    "UPDATE agents SET bulletin_memory = ? WHERE id = ?",
                    (json.dumps(existing, ensure_ascii=False), agent["id"]),
                )

    def _latest_bulletin_notice(self) -> str:
        bulletins = self.db.get_recent_bulletins(limit=3)
        if not bulletins:
            return "No recent manual bulletin board notice."
        parts = []
        for item in bulletins:
            title = str(item.get("title", "")).strip() or "Untitled Bulletin"
            content = str(item.get("content", "")).strip()
            parts.append(f"#{item.get('id')}: {title} | {content}")
        return " || ".join(parts)

    def _stabilize_bulletin_memory(
        self,
        current_bulletins: List[Dict[str, Any]],
        proposed_bulletins: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        current_bulletins = current_bulletins or []
        proposed_bulletins = proposed_bulletins or []

        merged: Dict[str, Dict[str, Any]] = {}
        for item in current_bulletins[-8:]:
            if not isinstance(item, dict):
                continue
            bulletin_id = str(item.get("bulletin_id") or item.get("id") or "")
            if not bulletin_id:
                continue
            decayed = dict(item)
            decayed["importance"] = round(max(0.0, self._as_float(item.get("importance"), 0.5) * 0.85), 2)
            decayed["retain"] = decayed["importance"] >= 0.2
            merged[bulletin_id] = decayed

        for item in proposed_bulletins[-8:]:
            if not isinstance(item, dict):
                continue
            bulletin_id = str(item.get("bulletin_id") or item.get("id") or "")
            if not bulletin_id:
                continue
            normalized = {
                "bulletin_id": int(bulletin_id) if bulletin_id.isdigit() else bulletin_id,
                "title": str(item.get("title", "")).strip(),
                "summary": str(item.get("summary", item.get("content", ""))).strip(),
                "importance": round(max(0.0, min(1.0, self._as_float(item.get("importance"), 0.5))), 2),
                "retain": bool(item.get("retain", True)),
                "last_reflection_day": self.clock.day_count,
            }
            merged[bulletin_id] = normalized

        stabilized = []
        for item in merged.values():
            if not item.get("retain", True):
                continue
            if self._as_float(item.get("importance"), 0.0) < 0.2:
                continue
            stabilized.append(item)

        stabilized.sort(key=lambda entry: float(entry.get("importance", 0.0)), reverse=True)
        return stabilized[:5]

    def _fundamental_event_trust_profile(self, role: str) -> Dict[str, Any]:
        role_lower = (role or "").lower()
        profile = {
            "trust": 0.55,
            "memory_importance": 0.52,
            "reaction_bias": "balanced",
            "event_focus": "price_and_story",
        }
        if any(token in role_lower for token in ["boss", "fundamentalist", "analyst", "老闆", "大戶", "分析師", "記者"]):
            profile.update(
                {
                    "trust": 0.88,
                    "memory_importance": 0.76,
                    "reaction_bias": "fundamentalist",
                    "event_focus": "valuation_and_earnings",
                }
            )
        elif any(token in role_lower for token in ["conservative", "indexer", "hedger", "中產"]):
            profile.update(
                {
                    "trust": 0.74,
                    "memory_importance": 0.64,
                    "reaction_bias": "quality_screen",
                    "event_focus": "yield_and_stability",
                }
            )
        elif any(token in role_lower for token in ["gambler", "fomo", "yolo", "moon", "panic", "小資"]):
            profile.update(
                {
                    "trust": 0.36,
                    "memory_importance": 0.42,
                    "reaction_bias": "headline_chasing",
                    "event_focus": "narrative_and_momentum",
                }
            )
        elif any(token in role_lower for token in ["news", "profit hunter", "visionary", "stop-loss"]):
            profile.update(
                {
                    "trust": 0.61,
                    "memory_importance": 0.55,
                    "reaction_bias": "selective_reader",
                    "event_focus": "salient_changes",
                }
            )
        return profile

    def _append_fundamental_memory_to_all_agents(self, events: List[Dict[str, Any]]):
        if not events:
            return
        with self.db._get_connection() as conn:
            agents = conn.execute("SELECT id, role, fundamental_memory FROM agents WHERE is_active = 1").fetchall()
            for agent in agents:
                role = str(agent["role"])
                profile = self._fundamental_event_trust_profile(role)
                existing = self._get_json_field(dict(agent), "fundamental_memory", [])
                for event in events:
                    base_impact = self._as_float(event.get("impact_score"), 0.0)
                    trust = self._as_float(profile["trust"], 0.5)
                    interpreted_impact = round(base_impact * (0.55 + trust), 3)
                    if interpreted_impact >= 0.18:
                        stance = "bullish"
                    elif interpreted_impact <= -0.18:
                        stance = "bearish"
                    else:
                        stance = "neutral"
                    existing.append(
                        {
                            "event_id": event.get("id"),
                            "symbol": event.get("symbol"),
                            "event_type": event.get("event_type"),
                            "title": event.get("event_title"),
                            "summary": event.get("summary"),
                            "trust": round(trust, 2),
                            "importance": round(
                                max(0.15, min(1.0, profile["memory_importance"] + abs(base_impact) * 0.25)), 2
                            ),
                            "interpreted_impact": interpreted_impact,
                            "stance": stance,
                            "reaction_bias": profile["reaction_bias"],
                            "event_focus": profile["event_focus"],
                            "retain": True,
                            "day": self.clock.day_count,
                            "tick": self.clock.current_tick,
                        }
                    )
                existing = self._trim_memory(existing, limit=10)
                conn.execute(
                    "UPDATE agents SET fundamental_memory = ? WHERE id = ?",
                    (json.dumps(existing, ensure_ascii=False), agent["id"]),
                )

    def _stabilize_relationship_memory(
        self,
        current_relationships: Dict[str, Any],
        proposed_relationships: Dict[str, Any],
    ) -> Dict[str, str]:
        """Merge relationship memories with the same guardrails as other memories.

        The prompt caps this at 3 keys, but the model is not obliged to obey;
        without this check an oversized dict would be written verbatim.
        """
        current_relationships = self._as_dict(current_relationships)
        proposed_relationships = self._as_dict(proposed_relationships)

        merged: Dict[str, str] = {}
        for source in (current_relationships, proposed_relationships):
            for key, value in list(source.items())[:6]:
                if not isinstance(key, str):
                    key = str(key)
                key = key.strip()[:64]
                if not key:
                    continue
                merged[key] = str(value)[:160]
        return dict(list(merged.items())[-3:])

    def _stabilize_fundamental_memory(
        self,
        current_items: List[Dict[str, Any]],
        proposed_items: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        current_items = current_items or []
        proposed_items = proposed_items or []

        merged: Dict[str, Dict[str, Any]] = {}
        for item in current_items[-10:]:
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("event_id") or "")
            symbol = str(item.get("symbol") or "")
            key = event_id or f"{symbol}:{item.get('event_type', 'unknown')}"
            decayed = dict(item)
            decayed["importance"] = round(max(0.0, self._as_float(item.get("importance"), 0.5) * 0.82), 2)
            decayed["retain"] = decayed["importance"] >= 0.2
            merged[key] = decayed

        for item in proposed_items[-10:]:
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("event_id") or "")
            symbol = str(item.get("symbol") or "")
            key = event_id or f"{symbol}:{item.get('event_type', 'unknown')}"
            normalized = dict(item)
            normalized["importance"] = round(max(0.0, min(1.0, self._as_float(item.get("importance"), 0.5))), 2)
            normalized["trust"] = round(max(0.0, min(1.0, self._as_float(item.get("trust"), 0.5))), 2)
            normalized["retain"] = bool(item.get("retain", True))
            normalized["last_reflection_day"] = self.clock.day_count
            merged[key] = normalized

        retained = [
            item
            for item in merged.values()
            if item.get("retain", True) and self._as_float(item.get("importance"), 0.0) >= 0.2
        ]
        retained.sort(
            key=lambda item: (
                self._as_float(item.get("importance"), 0.0),
                self._as_float(item.get("trust"), 0.0),
            ),
            reverse=True,
        )
        return retained[:8]

    def _compute_policy_metrics(self) -> Dict[str, Any]:
        prices = self.db.get_all_stock_prices()
        if not prices:
            return {
                "poverty_rate": 0.0,
                "cash_stress_rate": 0.0,
                "avg_market_return": 0.0,
                "top_stock_concentration": 0.0,
                "rich_agent_count": 0,
            }

        wealth = []
        stock_market_values = {symbol: 0.0 for symbol in prices.keys()}
        with self.db._get_connection() as conn:
            agents = conn.execute("SELECT id, cash, holdings FROM agents WHERE is_active = 1").fetchall()
            for row in agents:
                holdings = self._get_json_field(dict(row), "holdings", {})
                cash = float(row["cash"])
                stock_value = 0.0
                for symbol, quantity in holdings.items():
                    position_value = float(prices.get(symbol, 0.0)) * float(quantity)
                    stock_value += position_value
                    stock_market_values[symbol] = stock_market_values.get(symbol, 0.0) + position_value
                wealth.append(
                    {
                        "id": row["id"],
                        "cash": cash,
                        "stock_value": stock_value,
                        "total_assets": cash + stock_value,
                    }
                )

        poverty_threshold = self.policy_config["poverty_threshold"]
        cash_stress_line = self.base_living_cost * 2
        poverty_rate = sum(1 for item in wealth if item["total_assets"] < poverty_threshold) / max(len(wealth), 1)
        cash_stress_rate = sum(1 for item in wealth if item["cash"] < cash_stress_line) / max(len(wealth), 1)
        rich_agent_count = sum(1 for item in wealth if item["total_assets"] > self.policy_config["rich_threshold"])

        market_returns = []
        for symbol in prices.keys():
            history = self.db.get_price_history(symbol, 2)
            if len(history) >= 2 and float(history[0]["price"]) > 0:
                market_returns.append((float(history[-1]["price"]) - float(history[0]["price"])) / float(history[0]["price"]))
        avg_market_return = sum(market_returns) / len(market_returns) if market_returns else 0.0

        total_equity_value = sum(stock_market_values.values())
        top_stock_concentration = (
            max(stock_market_values.values()) / total_equity_value if total_equity_value > 0 else 0.0
        )

        return {
            "poverty_rate": round(poverty_rate, 4),
            "cash_stress_rate": round(cash_stress_rate, 4),
            "avg_market_return": round(avg_market_return, 4),
            "top_stock_concentration": round(top_stock_concentration, 4),
            "rich_agent_count": rich_agent_count,
        }

    def _determine_policy_action(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        if (
            metrics["poverty_rate"] > self.policy_config["poverty_rate_trigger"]
            or metrics["cash_stress_rate"] > self.policy_config["cash_stress_trigger"]
        ):
            new_living_cost = self.policy_config["living_cost_relief"]
            return {
                "policy_type": "relief",
                "title": "Central Relief Notice",
                "summary": f"Living cost base lowered from {self.base_living_cost:.2f} to {new_living_cost:.2f}, with wealth tax activated to ease poverty stress.",
                "new_living_cost": new_living_cost,
                "apply_wealth_tax": True,
            }

        if (
            metrics["avg_market_return"] > self.policy_config["avg_return_trigger"]
            and metrics["top_stock_concentration"] > self.policy_config["concentration_trigger"]
        ):
            new_living_cost = self.policy_config["living_cost_tighten"]
            return {
                "policy_type": "tighten",
                "title": "Central Tightening Notice",
                "summary": f"Living cost base raised from {self.base_living_cost:.2f} to {new_living_cost:.2f} to cool an overheated market.",
                "new_living_cost": new_living_cost,
                "apply_wealth_tax": False,
            }

        return {
            "policy_type": "neutral",
            "title": "Central Neutral Notice",
            "summary": "No intervention. Current market conditions remain within acceptable bounds.",
            "new_living_cost": self.policy_config["living_cost_baseline"],
            "apply_wealth_tax": False,
        }

    def _apply_central_policy(self) -> Dict[str, Any]:
        metrics = self._compute_policy_metrics()
        decision = self._determine_policy_action(metrics)
        living_cost_before = self.base_living_cost
        self.base_living_cost = float(decision["new_living_cost"])

        taxed_agents: List[Dict[str, Any]] = []
        personal_impacts: Dict[str, Dict[str, Any]] = {}
        total_tax_collected = 0.0
        if decision["apply_wealth_tax"]:
            rich_threshold = self.policy_config["rich_threshold"]
            wealth_tax_rate = self.policy_config["wealth_tax_rate"]
            with self.db._get_connection() as conn:
                agents = conn.execute("SELECT id, cash, holdings FROM agents WHERE is_active = 1").fetchall()
                prices = self.db.get_all_stock_prices()
                for row in agents:
                    holdings = self._get_json_field(dict(row), "holdings", {})
                    stock_value = sum(float(prices.get(symbol, 0.0)) * float(quantity) for symbol, quantity in holdings.items())
                    total_assets = float(row["cash"]) + stock_value
                    taxable = max(total_assets - rich_threshold, 0.0)
                    if taxable <= 0:
                        personal_impacts[row["id"]] = {
                            "taxed": False,
                            "tax_due": 0.0,
                            "actual_tax": 0.0,
                            "cash_before_tax": round(float(row["cash"]), 2),
                            "cash_after_tax": round(float(row["cash"]), 2),
                        }
                        continue
                    tax_due = round(taxable * wealth_tax_rate, 2)
                    cash_before_tax = round(float(row["cash"]), 2)
                    actual_tax = round(min(float(row["cash"]), tax_due), 2)
                    if actual_tax <= 0:
                        personal_impacts[row["id"]] = {
                            "taxed": False,
                            "tax_due": tax_due,
                            "actual_tax": 0.0,
                            "cash_before_tax": cash_before_tax,
                            "cash_after_tax": cash_before_tax,
                        }
                        continue
                    self.db.update_agent_cash(row["id"], -actual_tax)
                    cash_after_tax = round(cash_before_tax - actual_tax, 2)
                    total_tax_collected += actual_tax
                    taxed_agents.append(
                        {
                            "agent_id": row["id"],
                            "tax_due": tax_due,
                            "actual_tax": actual_tax,
                            "cash_before_tax": cash_before_tax,
                            "cash_after_tax": cash_after_tax,
                        }
                    )
                    personal_impacts[row["id"]] = {
                        "taxed": True,
                        "tax_due": tax_due,
                        "actual_tax": actual_tax,
                        "cash_before_tax": cash_before_tax,
                        "cash_after_tax": cash_after_tax,
                    }

        if decision["policy_type"] != "relief":
            with self.db._get_connection() as conn:
                agent_ids = [row["id"] for row in conn.execute("SELECT id FROM agents").fetchall()]
            for agent_id in agent_ids:
                personal_impacts.setdefault(
                    agent_id,
                    {
                        "taxed": False,
                        "tax_due": 0.0,
                        "actual_tax": 0.0,
                        "cash_before_tax": None,
                        "cash_after_tax": None,
                    },
                )

        actions = {
            "living_cost_before": round(living_cost_before, 2),
            "living_cost_after": round(self.base_living_cost, 2),
            "taxed_agents": taxed_agents,
            "total_tax_collected": round(total_tax_collected, 2),
        }

        if decision["policy_type"] != "neutral":
            issued_at = datetime.now()
            expires_at = issued_at + timedelta(days=1)
            policy_entry = {
                "day": self.clock.day_count,
                "tick": self.clock.current_tick,
                "policy_type": decision["policy_type"],
                "summary": decision["summary"],
                "impact_hint": (
                    "living costs increased; cash will tighten"
                    if decision["policy_type"] == "tighten"
                    else "living costs eased; wealthy agents faced extra taxation"
                ),
            }
            self._append_policy_memory_to_all_agents(policy_entry, personal_impacts=personal_impacts)
            self.forum.add_post("CentralBank", decision["summary"], 0.0, is_hot=True)
            self._set_active_policy(
                policy_type=decision["policy_type"],
                title=decision["title"],
                summary=decision["summary"],
                day=self.clock.day_count,
                issued_at=issued_at.isoformat(),
                expires_at=expires_at.isoformat(),
            )
        elif self.active_policy.get("policy_type") and not self._is_policy_notice_active():
                self._set_active_policy(None, None, None, None)

        self.db.record_policy_event(
            day=self.clock.day_count,
            tick=self.clock.current_tick,
            day_tick=self.clock.day_tick,
            policy_type=decision["policy_type"],
            title=decision["title"],
            summary=decision["summary"],
            metrics=metrics,
            actions=actions,
        )

        logger.info("[Policy] %s | %s", decision["policy_type"].upper(), decision["summary"])
        return {
            "decision": decision,
            "metrics": metrics,
            "actions": actions,
        }

    def _record_asset_snapshot(self, agent_id: str, source: str, note: str = "", prices: Dict[str, float] | None = None):
        state = self.db.get_agent_state(agent_id)
        if not state:
            return
        assets = self._compute_agent_assets(state, prices)
        self.db.record_asset_snapshot(
            agent_id=agent_id,
            tick=self.clock.current_tick,
            day=self.clock.day_count,
            day_tick=self.clock.day_tick,
            phase=self._current_phase_name(),
            source=source,
            cash=assets["cash"],
            stock_value=assets["stock_value"],
            total_assets=assets["total_assets"],
            holdings=assets["holdings"],
            note=note,
        )

    def _record_all_asset_snapshots(self, source: str, note: str = "", prices: Dict[str, float] | None = None):
        with self.db._get_connection() as conn:
            agents = conn.execute("SELECT id FROM agents WHERE is_active = 1").fetchall()
        for agent in agents:
            self._record_asset_snapshot(agent["id"], source=source, note=note, prices=prices)

    def _run_db_health_check(self, prices: Dict[str, float] | None = None) -> Dict[str, Any]:
        report = self.db.run_health_check(prices=prices)
        if report["status"] != "ok":
            logger.warning(
                "[HealthCheck] %s | negative_cash=%s negative_holdings=%s asset_formula_errors=%s",
                report["status"].upper(),
                report["negative_cash_agents"],
                report["negative_holding_entries"],
                report["asset_formula_errors"],
            )
        else:
            logger.info(
                "[HealthCheck] OK | agents=%s total_assets=%.2f",
                report["checked_agents"],
                report["total_assets"],
            )
        return report

    def _repair_account_integrity(self) -> Dict[str, Any]:
        repair = self.db.repair_account_integrity()
        if repair["fixed_negative_cash"] or repair["fixed_negative_holdings"]:
            logger.warning(
                "[IntegrityRepair] fixed_negative_cash=%s fixed_negative_holdings=%s",
                repair["fixed_negative_cash"],
                repair["fixed_negative_holdings"],
            )
        return repair

    def _sync_engine_from_db(self):
        catalog = self.db.get_stock_catalog()
        for symbol, item in catalog.items():
            config = self.engine.stock_config.setdefault(
                symbol,
                {"price": float(item["price"]), "beta": float(item["beta"])},
            )
            config["price"] = float(item["price"])
            config["beta"] = float(item["beta"])

    def _derive_growth_regime(self, growth_rate: float) -> str:
        if growth_rate >= 0.20:
            return "Hyper Growth"
        if growth_rate >= 0.10:
            return "Expansion"
        if growth_rate >= 0.02:
            return "Stable Growth"
        if growth_rate >= -0.04:
            return "Plateau"
        if growth_rate >= -0.12:
            return "Slowdown"
        return "Contraction"

    def _agent_information_tier(self, role: str) -> str:
        role_text = (role or "").lower()
        if any(key in role_text for key in ["boss", "fundamentalist", "analyst", "記者", "分析師", "老闆", "大戶"]):
            return "full"
        if any(key in role_text for key in ["indexer", "conservative", "hedger", "中產"]):
            return "focused"
        if any(key in role_text for key in ["news", "profit hunter", "visionary", "stop-loss"]):
            return "mixed"
        return "price_only"

    def _agent_watchlist(self, agent_id: str, state: Dict[str, Any]) -> List[str]:
        holdings = state.get("holdings", {}) or {}
        watched = [symbol for symbol, qty in holdings.items() if float(qty) > 0]
        watched = sorted(watched, key=lambda symbol: float(holdings.get(symbol, 0)), reverse=True)
        seed = sum(ord(ch) for ch in agent_id)
        ordered_symbols = sorted(COMPANY_PROFILES.keys(), key=lambda symbol: ((sum(ord(ch) for ch in symbol) + seed) % 97, symbol))
        for symbol in ordered_symbols:
            if symbol not in watched:
                watched.append(symbol)
        return watched

    def _summarize_event_for_tier(self, event: Dict[str, Any], tier: str) -> str:
        symbol = event["symbol"]
        title = event["event_title"]
        details = event.get("details", {}) or {}
        if tier == "full":
            return (
                f"{symbol}: {title}. EPS {details.get('old_forward_eps', 0):.2f} -> {details.get('new_forward_eps', 0):.2f}, "
                f"fair value {details.get('old_fair_value', 0):.2f} -> {details.get('new_fair_value', 0):.2f}, "
                f"dividend {details.get('old_dividend', 0):.2f} -> {details.get('new_dividend', 0):.2f}."
            )
        if tier in {"focused", "mixed"}:
            return f"{symbol}: {title}. {event['summary']}"
        return f"{symbol}: fresh company news may change value expectations."

    def _get_market_context_for_agent(self, agent_id: str, role: str, state: Dict[str, Any]) -> str:
        tier = self._agent_information_tier(role)
        catalog = self.db.get_stock_catalog()
        recent_events = self.db.get_recent_fundamental_events(limit=12)
        event_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
        for event in recent_events:
            event_by_symbol.setdefault(event["symbol"], []).append(event)
        watchlist = self._agent_watchlist(agent_id, state)
        focus_count = 4 if tier == "full" else 2 if tier == "focused" else 3 if tier == "mixed" else 1
        focus_symbols = set(watchlist[:focus_count])

        context = [
            "--- Market Real-time Data & Company Profiles ---",
            f"Simulation Time: {self.clock.get_virtual_time()}",
            f"Simulation Tick: {self.clock.current_tick} | Day Tick: {self.clock.day_tick} | Phase: {self._current_phase_name()}",
            f"Information Tier: {tier}",
        ]

        for symbol, profile in COMPANY_PROFILES.items():
            current_price = self.db.get_stock_price(symbol)
            stock_info = catalog.get(symbol, {})
            history = self.db.get_price_history(symbol, self.price_history_window)
            prices = [item["price"] for item in history]
            trend = "Up" if len(prices) >= 2 and prices[-1] >= prices[0] else "Down"
            if prices:
                rounded_prices = [f"{float(price):.2f}" for price in prices]
                short_window = rounded_prices[-5:]
                medium_window = rounded_prices[-15:]
                long_window = rounded_prices[-30:]
                price_history = (
                    f"Last 5: [{' -> '.join(short_window)}] | "
                    f"Last {len(medium_window)}: [{' -> '.join(medium_window)}] | "
                    f"Last {len(long_window)}: [{' -> '.join(long_window)}]"
                )
            else:
                price_history = "No history"

            context.extend(
                [
                    "",
                    f"{symbol}: {profile['name']}",
                    f"Business: {profile['business']}",
                    f"Vision: {profile['vision']}",
                    f"Current Price: ${current_price} | Trend: {trend} | History: [{price_history}]",
                ]
            )

            fair_value = float(stock_info.get("fair_value", 0.0) or 0.0)
            target_pe = float(stock_info.get("target_pe", 0.0) or 0.0)
            forward_eps = float(stock_info.get("forward_eps", 0.0) or 0.0)
            growth_rate = float(stock_info.get("growth_rate", 0.0) or 0.0)
            liquidation_value = float(stock_info.get("liquidation_value", 0.0) or 0.0)
            dividend_per_share = float(stock_info.get("dividend_per_share", 0.0) or 0.0)
            risk_score = float(stock_info.get("risk_score", 0.0) or 0.0)
            growth_regime = stock_info.get("growth_regime", "Stable")
            dividend_yield = ((dividend_per_share / current_price) * 100.0) if current_price else 0.0
            value_gap = (((current_price / fair_value) - 1.0) * 100.0) if fair_value > 0 else 0.0

            if tier == "full" or symbol in focus_symbols:
                context.extend(
                    [
                        (
                            f"Fundamentals: Forward EPS ${forward_eps:.2f} | Growth {growth_rate * 100:.1f}% | "
                            f"Regime {growth_regime} | Target PE {target_pe:.1f}"
                        ),
                        (
                            f"Valuation Anchors: Fair Value ${fair_value:.2f} | Liquidation Value ${liquidation_value:.2f} | "
                            f"Dividend/Share ${dividend_per_share:.2f} ({dividend_yield:.2f}% yield) | "
                            f"Value Gap {value_gap:+.1f}% | Risk {risk_score:.2f}"
                        ),
                    ]
                )
            elif tier == "mixed":
                context.append(
                    f"Partial fundamentals: Regime {growth_regime} | Yield {dividend_yield:.2f}% | Value Gap {value_gap:+.1f}%"
                )
            else:
                context.append("Limited fundamentals: you mainly observe price action, broad narrative, and a vague sense of valuation.")

            symbol_events = event_by_symbol.get(symbol, [])[-2:]
            if symbol_events:
                context.append(
                    "Recent company events: " + " | ".join(self._summarize_event_for_tier(event, tier) for event in symbol_events)
                )
            context.append("-" * 30)

        return "\n".join(context)

    def _ensure_stock_fundamentals(self):
        catalog = self.db.get_stock_catalog()
        for symbol, model in STOCK_FUNDAMENTAL_MODELS.items():
            item = catalog.get(symbol, {})
            current_forward_eps = float(item.get("forward_eps", 0.0) or 0.0)
            current_shares = float(item.get("shares_outstanding", 0.0) or 0.0)
            current_fair_value = float(item.get("fair_value", 0.0) or 0.0)
            current_dividend = float(item.get("dividend_per_share", 0.0) or 0.0)
            if current_forward_eps > 0 and current_shares > 0 and current_fair_value > 0 and current_dividend >= 0:
                continue

            target_pe = max(6.0, model["base_pe"] + (model["growth_rate"] * 18.0) - (model["risk_score"] * 6.0))
            fair_value = round(model["forward_eps"] * target_pe, 2)
            liquidation_value = round(max(0.01, fair_value * model["liquidation_anchor"]), 2)
            dividend_per_share = round(model["forward_eps"] * model["dividend_payout_ratio"], 2)
            self.db.update_stock_fundamentals(
                symbol,
                {
                    "shares_outstanding": model["shares_outstanding"],
                    "forward_eps": model["forward_eps"],
                    "growth_rate": model["growth_rate"],
                    "target_pe": round(target_pe, 2),
                    "fair_value": fair_value,
                    "liquidation_value": liquidation_value,
                    "dividend_per_share": dividend_per_share,
                    "risk_score": model["risk_score"],
                    "sentiment_beta": model["sentiment_beta"],
                    "growth_regime": self._derive_growth_regime(model["growth_rate"]),
                    "eps": model["forward_eps"],
                },
            )

    def _advance_stock_fundamentals(self):
        catalog = self.db.get_stock_catalog()
        for symbol, model in STOCK_FUNDAMENTAL_MODELS.items():
            item = catalog.get(symbol)
            if not item:
                continue

            prior_growth = float(item.get("growth_rate", model["growth_rate"]) or model["growth_rate"])
            baseline_growth = float(model["growth_rate"])
            drift = random.uniform(-0.035, 0.035)
            regime_shift = random.uniform(-0.02, 0.02) if random.random() < 0.22 else 0.0
            new_growth = max(-0.18, min(0.35, (prior_growth * 0.65) + (baseline_growth * 0.35) + drift + regime_shift))

            prior_forward_eps = float(item.get("forward_eps", model["forward_eps"]) or model["forward_eps"])
            eps_noise = random.uniform(-0.04, 0.04)
            new_forward_eps = max(0.05, prior_forward_eps * (1 + new_growth + eps_noise))

            risk_score = max(
                0.1,
                min(0.95, float(item.get("risk_score", model["risk_score"]) or model["risk_score"]) + random.uniform(-0.03, 0.03)),
            )
            sentiment_beta = max(
                0.5,
                min(2.0, float(item.get("sentiment_beta", model["sentiment_beta"]) or model["sentiment_beta"]) + random.uniform(-0.05, 0.05)),
            )
            target_pe = max(
                6.0,
                min(
                    48.0,
                    model["base_pe"]
                    + (new_growth * 30.0)
                    - (risk_score * 8.0)
                    + random.uniform(-model["pe_volatility"], model["pe_volatility"]),
                ),
            )
            fair_value = round(new_forward_eps * target_pe, 2)
            liquidation_value = round(
                max(
                    0.01,
                    (float(item.get("liquidation_value", 0.0) or (fair_value * model["liquidation_anchor"])) * 0.55)
                    + ((fair_value * model["liquidation_anchor"]) * 0.45)
                    + random.uniform(-0.5, 0.5),
                ),
                2,
            )
            dividend_per_share = round(max(0.0, new_forward_eps * model["dividend_payout_ratio"]), 2)

            self.db.update_stock_fundamentals(
                symbol,
                {
                    "shares_outstanding": float(item.get("shares_outstanding", model["shares_outstanding"]) or model["shares_outstanding"]),
                    "forward_eps": round(new_forward_eps, 2),
                    "growth_rate": round(new_growth, 4),
                    "target_pe": round(target_pe, 2),
                    "fair_value": fair_value,
                    "liquidation_value": liquidation_value,
                    "dividend_per_share": dividend_per_share,
                    "risk_score": round(risk_score, 3),
                    "sentiment_beta": round(sentiment_beta, 3),
                    "growth_regime": self._derive_growth_regime(new_growth),
                    "eps": round(new_forward_eps, 2),
                },
            )

    def _draw_fundamental_event_type(self) -> str | None:
        probabilities = self.fundamental_event_config.get("probabilities", {})
        roll = random.random()
        cumulative = 0.0
        for event_type, probability in probabilities.items():
            cumulative += max(0.0, float(probability or 0.0))
            if roll < cumulative:
                return event_type
        return None

    def _scale_multiplier_range(self, bounds: Tuple[float, float], scale: float) -> Tuple[float, float]:
        low, high = bounds
        scaled_low = 1.0 + ((low - 1.0) * scale)
        scaled_high = 1.0 + ((high - 1.0) * scale)
        return scaled_low, scaled_high

    def _scale_delta_range(self, bounds: Tuple[float, float], scale: float) -> Tuple[float, float]:
        low, high = bounds
        return low * scale, high * scale

    def _trigger_fundamental_events(self) -> List[Dict[str, Any]]:
        if not self.fundamental_event_config.get("enabled", True):
            return []
        catalog = self.db.get_stock_catalog()
        event_results: List[Dict[str, Any]] = []
        impact_scale = max(0.1, float(self.fundamental_event_config.get("impact_scale", 1.0) or 1.0))
        for symbol, item in catalog.items():
            event_type = self._draw_fundamental_event_type()
            if not event_type:
                continue

            template = FUNDAMENTAL_EVENT_TEMPLATES[event_type]
            event_ranges = self.fundamental_event_config.get("ranges", {}).get(event_type, {})
            old_forward_eps = float(item.get("forward_eps", 0.0) or 0.0)
            old_growth = float(item.get("growth_rate", 0.0) or 0.0)
            old_target_pe = float(item.get("target_pe", 0.0) or 0.0)
            old_fair_value = float(item.get("fair_value", 0.0) or 0.0)
            old_dividend = float(item.get("dividend_per_share", 0.0) or 0.0)
            liquidation_value = float(item.get("liquidation_value", 0.0) or 0.0)
            risk_score = float(item.get("risk_score", 0.0) or 0.0)
            sentiment_beta = float(item.get("sentiment_beta", 1.0) or 1.0)
            shares_outstanding = float(item.get("shares_outstanding", 1.0) or 1.0)

            eps_multiplier = random.uniform(
                *self._scale_multiplier_range(tuple(event_ranges.get("eps_multiplier", (1.0, 1.0))), impact_scale)
            )
            growth_delta = random.uniform(
                *self._scale_delta_range(tuple(event_ranges.get("growth_delta", (0.0, 0.0))), impact_scale)
            )
            pe_delta = random.uniform(
                *self._scale_delta_range(tuple(event_ranges.get("pe_delta", (0.0, 0.0))), impact_scale)
            )
            dividend_multiplier = random.uniform(
                *self._scale_multiplier_range(tuple(event_ranges.get("dividend_multiplier", (1.0, 1.0))), impact_scale)
            )

            new_forward_eps = max(0.05, old_forward_eps * eps_multiplier)
            new_growth = max(-0.20, min(0.38, old_growth + growth_delta))
            new_target_pe = max(6.0, min(52.0, old_target_pe + pe_delta))
            new_dividend = max(0.0, old_dividend * dividend_multiplier)
            if event_type in {"earnings_beat", "earnings_miss", "guidance_raise", "guidance_cut"} and new_dividend == 0.0:
                payout_ratio = STOCK_FUNDAMENTAL_MODELS[symbol]["dividend_payout_ratio"]
                new_dividend = max(0.0, new_forward_eps * payout_ratio)
            new_fair_value = round(new_forward_eps * new_target_pe, 2)

            self.db.update_stock_fundamentals(
                symbol,
                {
                    "shares_outstanding": shares_outstanding,
                    "forward_eps": round(new_forward_eps, 2),
                    "growth_rate": round(new_growth, 4),
                    "target_pe": round(new_target_pe, 2),
                    "fair_value": new_fair_value,
                    "liquidation_value": liquidation_value,
                    "dividend_per_share": round(new_dividend, 2),
                    "risk_score": round(risk_score, 3),
                    "sentiment_beta": round(sentiment_beta, 3),
                    "growth_regime": self._derive_growth_regime(new_growth),
                    "eps": round(new_forward_eps, 2),
                },
            )

            summary = (
                f"{symbol} reported a fundamental update. Forward EPS is now {new_forward_eps:.2f}, "
                f"fair value is now {new_fair_value:.2f}, and dividend/share is {new_dividend:.2f}."
            )
            details = {
                "old_forward_eps": round(old_forward_eps, 2),
                "new_forward_eps": round(new_forward_eps, 2),
                "old_growth_rate": round(old_growth, 4),
                "new_growth_rate": round(new_growth, 4),
                "old_target_pe": round(old_target_pe, 2),
                "new_target_pe": round(new_target_pe, 2),
                "old_fair_value": round(old_fair_value, 2),
                "new_fair_value": round(new_fair_value, 2),
                "old_dividend": round(old_dividend, 2),
                "new_dividend": round(new_dividend, 2),
            }
            event_id = self.db.record_fundamental_event(
                day=self.clock.day_count,
                tick=self.clock.current_tick,
                day_tick=self.clock.day_tick,
                phase=self._current_phase_name(),
                symbol=symbol,
                event_type=event_type,
                event_title=template["title"],
                summary=summary,
                impact_score=max(-1.0, min(1.0, float(template["impact"]) * impact_scale)),
                details=details,
            )
            self.forum.add_post(
                "MarketDesk",
                f"{symbol}: {template['title']}. Investors are debating whether the new outlook changes the stock's real value.",
                sentiment=max(-1.0, min(1.0, float(template["impact"]) * impact_scale)),
                is_hot=abs(float(template["impact"]) * impact_scale) >= 0.55,
            )
            event_results.append(
                {
                    "id": event_id,
                    "symbol": symbol,
                    "event_type": event_type,
                    "event_title": template["title"],
                    "impact_score": max(-1.0, min(1.0, float(template["impact"]) * impact_scale)),
                    "summary": summary,
                }
            )
        self._append_fundamental_memory_to_all_agents(event_results)
        return event_results

    def _auto_split_overpriced_stocks(self):
        if not self.stock_split_config.get("enabled", True):
            return []
        results = []
        trigger_price = float(self.stock_split_config["trigger_price"])
        split_ratio = int(self.stock_split_config["default_ratio"])
        prices = self.db.get_all_stock_prices()
        for symbol, price in prices.items():
            if float(price) <= trigger_price:
                continue
            result = apply_stock_split(
                db=self.db,
                forum=self.forum,
                symbol=symbol,
                split_ratio=split_ratio,
                tick=self.clock.current_tick,
                day=self.clock.day_count,
                day_tick=self.clock.day_tick,
                phase=self._current_phase_name(),
                source="auto_threshold",
                reason=f"Auto split because {symbol} price exceeded ${trigger_price:.2f}",
            )
            self.engine.stock_config[symbol]["price"] = float(result["new_price"])
            self.engine.day_open_prices[symbol] = float(result["new_price"])
            logger.info("[StockSplit] AUTO | %s", result["summary"])
            results.append(result)
        return results

    def _write_daily_audit_log(self, policy_result: Dict[str, Any], health_report: Dict[str, Any], prices: Dict[str, float]):
        decision = policy_result["decision"]
        actions = policy_result["actions"]
        with self.db._get_connection() as conn:
            top_agents = conn.execute(
                "SELECT id, role, cash, holdings FROM agents WHERE is_active = 1 ORDER BY cash DESC LIMIT 3"
            ).fetchall()

        top_lines = []
        for row in top_agents:
            holdings = self._get_json_field(dict(row), "holdings", {})
            stock_value = self._compute_stock_value(holdings, prices)
            total_assets = round(float(row["cash"]) + stock_value, 2)
            top_lines.append(f"- {row['id']} ({row['role']}): total_assets=${total_assets:,.2f}")

        summary = (
            f"Health={health_report['status'].upper()} | "
            f"Policy={decision['policy_type']} | "
            f"LivingCost={actions['living_cost_before']:.2f}->{actions['living_cost_after']:.2f} | "
            f"CheckedAgents={health_report['checked_agents']}"
        )

        self.db.record_daily_audit(
            day=self.clock.day_count,
            tick=self.clock.current_tick,
            day_tick=self.clock.day_tick,
            phase=self._current_phase_name(),
            health_status=health_report["status"],
            summary=summary,
            stats={
                "policy_type": decision["policy_type"],
                "policy_summary": decision["summary"],
                "living_cost_before": actions["living_cost_before"],
                "living_cost_after": actions["living_cost_after"],
                "total_tax_collected": actions["total_tax_collected"],
                "checked_agents": health_report["checked_agents"],
                "negative_cash_agents": health_report["negative_cash_agents"],
                "negative_holding_entries": health_report["negative_holding_entries"],
                "asset_formula_errors": health_report["asset_formula_errors"],
                "total_cash": health_report["total_cash"],
                "total_stock_value": health_report["total_stock_value"],
                "total_assets": health_report["total_assets"],
                "prices": prices,
            },
            issues=health_report["issues"],
        )

        with open(self.daily_audit_log_path, "a", encoding="utf-8") as f:
            f.write(f"\n## Day {self.clock.day_count} | Tick {self.clock.current_tick} | {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            f.write(f"- Health Status: {health_report['status'].upper()}\n")
            f.write(f"- Policy: {decision['policy_type']} | {decision['summary']}\n")
            f.write(f"- Living Cost Base: {actions['living_cost_before']:.2f} -> {actions['living_cost_after']:.2f}\n")
            f.write(f"- Total Tax Collected: ${actions['total_tax_collected']:,.2f}\n")
            f.write(
                f"- DB Health: checked={health_report['checked_agents']}, "
                f"negative_cash={health_report['negative_cash_agents']}, "
                f"negative_holdings={health_report['negative_holding_entries']}, "
                f"asset_formula_errors={health_report['asset_formula_errors']}\n"
            )
            f.write(f"- Aggregate Assets: cash=${health_report['total_cash']:,.2f}, stock=${health_report['total_stock_value']:,.2f}, total=${health_report['total_assets']:,.2f}\n")
            f.write("- Top Agents:\n")
            for line in top_lines:
                f.write(f"{line}\n")
            if health_report["issues"]:
                f.write("- Issues:\n")
                for issue in health_report["issues"][:10]:
                    f.write(f"  - {issue['type']}: {issue['message']}\n")
            else:
                f.write("- Issues: none\n")

    def setup_world(self):
        logger.info("Server starting: initializing world state.")
        self._write_status(status="initializing", message="Loading world state")

        stocks = [("VDA", 100.0, 0.02), ("ITE", 30.0, 0.12), ("SLA", 150.0, 0.07), ("SMC", 50.0, 0.04)]
        added_stocks = []
        for symbol, price, beta in stocks:
            existed_before = self.db.get_stock_price(symbol) is not None
            initial_eps = float(STOCK_FUNDAMENTAL_MODELS.get(symbol, {}).get("forward_eps", 0.0))
            self.db.init_stock(symbol, price, beta, eps=initial_eps)
            current_price = float(self.db.get_stock_price(symbol) or price)
            self.engine.stock_config[symbol] = {"price": current_price, "beta": beta}
            if not existed_before:
                added_stocks.append(symbol)
            if not self.db.get_price_history(symbol, 1):
                self.db.record_price(
                    symbol,
                    current_price,
                    volume=0,
                    tick=self.clock.current_tick,
                    day=self.clock.day_count,
                    day_tick=self.clock.day_tick,
                    phase=self._current_phase_name(),
                )
        self._ensure_stock_fundamentals()

        if not os.path.exists(self.agents_dir):
            # A fresh checkout ships with an empty agents/ directory. Creating it
            # keeps setup_world reaching the final "ready" status instead of
            # returning early and leaving the status stuck on "booting".
            logger.warning("Agents directory not found, creating it: %s", self.agents_dir)
            os.makedirs(self.agents_dir, exist_ok=True)

        added_agents = []
        for filename in os.listdir(self.agents_dir):
            if not filename.endswith(".json"):
                continue

            # One malformed agent file must not abort world initialization.
            try:
                with open(os.path.join(self.agents_dir, filename), "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                agent_id = cfg["id"]
                role = cfg["role"]
                cash = float(cfg["cash"])
                salary = float(cfg["salary"])
                cost_multiplier = float(cfg["cost_multiplier"])
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                logger.exception("Skipping invalid agent config: %s", filename)
                continue

            if not isinstance(agent_id, str) or not agent_id.strip():
                logger.error("Skipping agent config without a valid id: %s", filename)
                continue

            if self.db.get_agent_state(agent_id):
                continue

            initial_holdings = cfg.get("initial_holdings")
            if not initial_holdings:
                initial_holdings = {
                    symbol: random.randint(10, 100) for symbol in self.engine.stock_config.keys()
                }

            with self.db._get_connection() as conn:
                conn.execute(
                    "INSERT INTO agents (id, role, cash, salary, cost_multiplier, holdings) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        agent_id,
                        role,
                        cash,
                        salary,
                        cost_multiplier,
                        json.dumps(initial_holdings),
                    ),
                )
            added_agents.append(agent_id)

        if added_agents or added_stocks:
            logger.info(
                "World initialized. Added agents=%s Added stocks=%s Existing state preserved.",
                len(added_agents),
                len(added_stocks),
            )
            self._record_all_asset_snapshots(
                source="setup",
                note="World setup applied for newly added entities",
                prices=self.db.get_all_stock_prices(),
            )
        else:
            logger.info(
                "World state resumed without resetting prices or agent portfolios. Day=%s Tick=%s",
                self.clock.day_count,
                self.clock.current_tick,
            )
        self._save_runtime_state()
        self._write_status(
            status="ready",
            message="World initialized" if (added_agents or added_stocks) else "World state resumed",
            extra={"resumed": not (added_agents or added_stocks)},
        )

    def write_trade_log(self, settlements, prices):
        """Append one row per settlement to trade_log.md.

        ``settlements`` is a list of dicts with agent/action/stock,
        ``requested_amount``, ``executed_amount`` and ``refunded_amount``;
        rows are reported as what actually executed so a round where nothing
        matched does not read like a round full of trades.
        """
        with open(self.trade_log_path, "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')} | Tick {self.clock.current_tick}\n")
            f.write("| Agent | Action | Stock | Requested | Executed | Status |\n")
            f.write("|---|---|---|---|---|---|\n")
            for settlement in settlements:
                if isinstance(settlement, dict):
                    get = settlement.get
                else:  # tolerate raw settlement objects
                    def get(key, default=None):
                        return getattr(settlement, key, default)
                executed = int(get("executed_amount", 0) or 0)
                requested = int(get("requested_amount", executed) or 0)
                if executed <= 0:
                    status = "No fill"
                elif executed < requested:
                    status = "Partial"
                else:
                    status = "Matched"
                f.write(
                    f"| {get('agent_id')} | {get('action')} | {get('stock')} | "
                    f"{requested} | {executed} | {status} |\n"
                )
            f.write("\n**Price Changes:**\n")
            for symbol, price in prices.items():
                f.write(f"- {symbol}: {price}\n")
            f.write("\n---\n")

    def _get_market_context(self) -> str:
        return self._get_market_context_for_agent(
            agent_id="SYSTEM",
            role="Boss",
            state={"holdings": {}},
        )

    def _build_position_consistency_context(self, state: Dict[str, Any]) -> str:
        holdings = self._as_dict(state.get("holdings"))
        non_zero = {symbol: qty for symbol, qty in holdings.items() if self._as_float(qty, 0.0) > 0}
        trading_memory = [item for item in self._as_list(self._get_json_field(state, "trading_memory", [])) if isinstance(item, dict)]
        recent_actions = []
        for item in trading_memory[-5:]:
            action = str(item.get("action", "hold")).upper()
            stock = item.get("stock", "UNKNOWN")
            qty = item.get("exec", item.get("requested_exec", 0))
            recent_actions.append(f"{action} {stock} x{qty}")
        return (
            f"Current live holdings: {non_zero if non_zero else 'No open positions'}.\n"
            f"Recent executed trading stance: {recent_actions if recent_actions else 'No recent executed trades'}.\n"
            "Do not publicly cheerlead or attack a stock in a way that clearly contradicts your current holdings or very recent trades "
            "unless you explicitly explain why your stance is changing."
        )

    @staticmethod
    def _safe_post_content(raw_content: Any) -> str:
        """Forum content must be plain text of a bounded length."""
        content = raw_content if isinstance(raw_content, str) else ""
        content = content.strip()
        if not content:
            return "No content"
        return content[:600]

    @staticmethod
    def _safe_parent_id(raw_target: Any) -> Optional[int]:
        if isinstance(raw_target, bool):
            return None
        if isinstance(raw_target, int):
            return raw_target
        if isinstance(raw_target, str) and raw_target.isdigit():
            return int(raw_target)
        return None

    @staticmethod
    def _as_float(value: Any, default: float) -> float:
        """Coerce untrusted model/DB values to float without raising."""
        if isinstance(value, bool) or value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _as_dict(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _as_list(value: Any) -> List[Any]:
        return value if isinstance(value, list) else []

    @staticmethod
    def _iter_trade_dicts(raw_trades: Any) -> List[Dict[str, Any]]:
        """Model output is untrusted: only keep entries that are real objects."""
        if not isinstance(raw_trades, list):
            return []
        return [trade for trade in raw_trades if isinstance(trade, dict)]

    def _sanitize_trade_plan(self, agent_id: str, trades: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
        state = self.db.get_agent_state(agent_id)
        if not state:
            return [], [f"{agent_id}: missing state"]

        remaining_cash = float(state["cash"])
        minimum_cash_reserve = self._minimum_cash_reserve(state)
        remaining_holdings = dict(state["holdings"])
        sanitized_trades = []
        notes: List[str] = []

        for raw_trade in trades or []:
            if not isinstance(raw_trade, dict):
                continue
            action = str(raw_trade.get("action", "hold")).lower()
            stock = str(raw_trade.get("stock", "")).upper()
            amount = self._normalize_trade_amount(raw_trade.get("amount", 0))

            if action not in {"buy", "sell"} or amount <= 0:
                continue
            if stock not in self.engine.stock_config:
                notes.append(f"{agent_id}: skipped unknown stock {stock}")
                continue

            price = float(self.db.get_stock_price(stock) or self.engine.stock_config[stock]["price"])
            if action == "buy":
                spendable_cash = max(0.0, remaining_cash - minimum_cash_reserve)
                affordable_amount = int(spendable_cash // price) if price > 0 else 0
                executable_amount = min(amount, affordable_amount)
                if executable_amount <= 0:
                    notes.append(
                        f"{agent_id}: insufficient free cash for {stock} after keeping reserve {minimum_cash_reserve:.2f}"
                    )
                    continue
                remaining_cash -= executable_amount * price
            else:
                owned_amount = int(remaining_holdings.get(stock, 0))
                executable_amount = min(amount, owned_amount)
                if executable_amount <= 0:
                    notes.append(f"{agent_id}: insufficient holdings for {stock}")
                    continue
                remaining_holdings[stock] = owned_amount - executable_amount

            if executable_amount < amount:
                notes.append(f"{agent_id}: reduced {action} {stock} from {amount} to {executable_amount}")

            sanitized_trades.append(
                {
                    "agent_id": agent_id,
                    "stock": stock,
                    "action": action,
                    "amount": executable_amount,
                }
            )

        return sanitized_trades, notes

    def _record_trading_memory(self, agent_id: str, stock: str, action: str, executed_amount: int, requested_amount: int, price: float):
        with self.db._get_connection() as conn:
            row = conn.execute("SELECT trading_memory FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if not row:
                return
            trading_memory = self._as_list(self._get_json_field(dict(row), "trading_memory", []))
            trading_memory.append(
                {
                    "tick": self.clock.current_tick,
                    "stock": stock,
                    "action": action,
                    "exec": executed_amount,
                    "requested_exec": requested_amount,
                    "price": price,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            trading_memory = trading_memory[-20:]
            conn.execute("UPDATE agents SET trading_memory = ? WHERE id = ?", (json.dumps(trading_memory), agent_id))

    def _apply_settlement(self, settlement, price: float) -> int:
        """Execute one settlement and return the number of shares actually traded.

        Returning the executed amount lets callers report real fills instead of
        counting orders that were skipped (no counterparty, no usable price,
        agent cannot afford / does not hold the shares).
        """
        agent = self.db.get_agent_state(settlement.agent_id)
        if not agent:
            logger.warning("Settlement skipped for missing agent %s", settlement.agent_id)
            return 0

        # A zero/unknown price would either buy nothing or hand shares away for free.
        if price <= 0:
            logger.warning(
                "Settlement skipped: no usable price for %s (price=%s)",
                settlement.stock,
                price,
            )
            return 0

        # Nothing matched in the lottery round (e.g. no counterparty): this is
        # normal, not an affordability problem, so report it as such.
        if settlement.executed_amount <= 0:
            logger.info(
                "Settlement skipped: nothing matched for %s %s %s",
                settlement.agent_id,
                settlement.action,
                settlement.stock,
            )
            return 0

        before_assets = self._compute_agent_assets(agent)
        holdings_before = dict(agent["holdings"])

        if settlement.action == "buy":
            max_affordable = int(agent["cash"] // price)
            executed_amount = min(settlement.executed_amount, max_affordable)
            if executed_amount <= 0:
                logger.warning("Settlement blocked: %s cannot afford %s", settlement.agent_id, settlement.stock)
                return 0
            # Cash and shares move together in a single transaction.
            self.db.apply_trade_settlement(
                settlement.agent_id,
                -(executed_amount * price),
                {settlement.stock: executed_amount},
            )
        else:
            owned_amount = int(agent["holdings"].get(settlement.stock, 0))
            executed_amount = min(settlement.executed_amount, owned_amount)
            if executed_amount <= 0:
                logger.warning("Settlement blocked: %s has no %s to sell", settlement.agent_id, settlement.stock)
                return 0
            self.db.apply_trade_settlement(
                settlement.agent_id,
                executed_amount * price,
                {settlement.stock: -executed_amount},
            )

        updated_agent = self.db.get_agent_state(settlement.agent_id)
        after_assets = self._compute_agent_assets(updated_agent)

        self.db.record_trade_asset_change(
            tick=self.clock.current_tick,
            day=self.clock.day_count,
            day_tick=self.clock.day_tick,
            agent_id=settlement.agent_id,
            stock=settlement.stock,
            action=settlement.action,
            requested_amount=settlement.executed_amount + settlement.refunded_amount,
            executed_amount=executed_amount,
            price=price,
            cash_before=before_assets["cash"],
            cash_after=after_assets["cash"],
            stock_value_before=before_assets["stock_value"],
            stock_value_after=after_assets["stock_value"],
            total_assets_before=before_assets["total_assets"],
            total_assets_after=after_assets["total_assets"],
            holdings_before=holdings_before,
            holdings_after=after_assets["holdings"],
        )

        self._record_trading_memory(
            settlement.agent_id,
            settlement.stock,
            settlement.action,
            executed_amount,
            settlement.executed_amount + settlement.refunded_amount,
            price,
        )
        self._record_asset_snapshot(
            settlement.agent_id,
            source="trade",
            note=f"{settlement.action} {settlement.stock} x{executed_amount}",
        )
        return executed_amount

    def run_market_round(self):
        logger.info("%s | Trading round start.", datetime.now().strftime("%H:%M"))
        self._load_agent_traits()
        self._sync_engine_from_db()
        self._write_status(status="running", message="Market round started", extra={"round_type": "market"})
        if self.clock.day_tick == 0:
            for symbol, config in self.engine.stock_config.items():
                self.engine.day_open_prices[symbol] = config["price"]
            logger.info("Day opening prices synchronized.")

        policy_notice = self._latest_policy_notice()
        bulletin_notice = self._latest_bulletin_notice()
        lottery_barrel = []

        with self.db._get_connection() as conn:
            agents = conn.execute("SELECT id, role FROM agents WHERE is_active = 1").fetchall()

        market_jobs: List[Dict[str, Any]] = []
        market_job_meta: List[Dict[str, Any]] = []
        for agent in agents:
            agent_id, role = agent["id"], agent["role"]
            state = self.db.get_agent_state(agent_id)
            traits = self.agent_traits.get(agent_id, "Standard investor")
            trust_profile = self._fundamental_event_trust_profile(role)
            memory_context = self._build_memory_context(state, mode="market")
            market_context = self._get_market_context_for_agent(agent_id, role, state)
            minimum_cash_reserve = self._minimum_cash_reserve(state)
            portfolio_context = (
                f"Your Wallet: ${state['cash']:.2f} | Your Holdings: {state['holdings']} | "
                f"Required Cash Reserve: ${minimum_cash_reserve:.2f}\n"
            )
            consistency_context = self._build_position_consistency_context(state)

            prompt = (
                f"Your Traits: {traits}\n"
                f"Simulation Time: {self.clock.get_virtual_time()}\n"
                f"Simulation Tick: {self.clock.current_tick} | Day Tick: {self.clock.day_tick} | Phase: {self._current_phase_name()}\n"
                f"--- Your Memories ---\n{memory_context}\n"
                f"Latest Policy Notice: {policy_notice}\n"
                f"Latest Bulletin Board Notice: {bulletin_notice}\n\n"
                f"{portfolio_context}{consistency_context}\n\n{market_context}\n"
                f"Instructions: Analyze the company visions and current prices. "
                f"As a {role}, which company's vision aligns with your values or financial goals?\n\n"
                f"Treat policy, forum, and price action as competing signals rather than blindly following any single one.\n"
                f"Weigh valuation anchors too: forward earnings, growth regime, target PE, dividend support, fair value, and liquidation value.\n"
                f"Your fundamental-event trust profile is {trust_profile['reaction_bias']} with trust {trust_profile['trust']:.2f}; "
                f"let that affect how heavily you act on company events versus price action or social noise.\n"
                f"You must keep cash above your required reserve. This reserve must always cover at least one full day of living cost.\n"
                f"If you are already wealthy, you must also keep a strategic cash buffer and must not go all-in to dodge taxes or policy effects.\n"
                f"Keep your decision consistent with your long-term beliefs unless the evidence clearly changed.\n"
                f"If another agent recently influenced you, factor that into your reasoning without ignoring your own style.\n\n"
                f"Return JSON in this format only:\n"
                f'{{"trades": [{{"action": "buy"|"sell"|"hold", "stock": "...", "amount": X}}], "thought_process": "..."}}\n'
                f"- Buy amount must be a non-negative integer.\n"
                f"- Sell amount must be a non-negative integer.\n"
                f"- Hold means no order."
            )
            market_jobs.append(
                {
                    "agent_id": agent_id,
                    "role": role,
                    "mode": "market",
                    "prompt": prompt,
                }
            )
            market_job_meta.append({"agent_id": agent_id, "role": role})

        market_decisions = self._run_llm_jobs(market_jobs)
        market_fallback_count = 0
        for meta, decision in zip(market_job_meta, market_decisions):
            agent_id = meta["agent_id"]
            role = meta["role"]
            if self._is_fallback_decision(decision):
                market_fallback_count += 1
            trades_summary = ", ".join(
                f"{str(trade.get('action', '?')).upper()} {trade.get('stock')} x{trade.get('amount')}"
                for trade in self._iter_trade_dicts(decision.get("trades"))
            )
            logger.info("[Market] %s (%s) -> %s | %s", agent_id, role, trades_summary or "HOLD", decision.get("thought_process"))

            safe_trades, notes = self._sanitize_trade_plan(agent_id, self._iter_trade_dicts(decision.get("trades")))
            for note in notes:
                logger.info("[TradeGuard] %s", note)
            lottery_barrel.extend(safe_trades)

        if lottery_barrel:
            order_volume_by_symbol: Dict[str, Dict[str, int]] = {}
            for order in lottery_barrel:
                symbol = order["stock"]
                bucket = order_volume_by_symbol.setdefault(symbol, {"buy": 0, "sell": 0})
                bucket[order["action"]] += int(order["amount"])
            settlements, new_prices = self.engine.process_orders(lottery_barrel)
            traded_volume_by_symbol: Dict[str, int] = {}
            settlement_rows = []
            for settlement in settlements:
                price = float(self.db.get_stock_price(settlement.stock) or 0.0)
                executed_amount = self._apply_settlement(settlement, price)
                # Count what really changed hands, not what was ordered.
                traded_volume_by_symbol[settlement.stock] = (
                    traded_volume_by_symbol.get(settlement.stock, 0) + executed_amount
                )
                settlement_rows.append(
                    {
                        "agent_id": settlement.agent_id,
                        "action": settlement.action,
                        "stock": settlement.stock,
                        "requested_amount": settlement.executed_amount + settlement.refunded_amount,
                        "executed_amount": executed_amount,
                        "refunded_amount": settlement.refunded_amount,
                    }
                )
            executed_settlements = sum(1 for row in settlement_rows if row["executed_amount"] > 0)

            for symbol, price in new_prices.items():
                open_price = float(self.engine.stock_config[symbol]["price"])
                self.db.record_price(
                    symbol,
                    price,
                    volume=traded_volume_by_symbol.get(symbol, 0),
                    tick=self.clock.current_tick,
                    day=self.clock.day_count,
                    day_tick=self.clock.day_tick,
                    phase=self._current_phase_name(),
                )
                self.db.record_market_round_stat(
                    tick=self.clock.current_tick,
                    day=self.clock.day_count,
                    day_tick=self.clock.day_tick,
                    phase=self._current_phase_name(),
                    symbol=symbol,
                    buy_volume=order_volume_by_symbol.get(symbol, {}).get("buy", 0),
                    sell_volume=order_volume_by_symbol.get(symbol, {}).get("sell", 0),
                    matched_volume=traded_volume_by_symbol.get(symbol, 0),
                    open_price=open_price,
                    close_price=price,
                )
                self.engine.stock_config[symbol]["price"] = price

            self._record_all_asset_snapshots(
                source="market_round",
                note="Marked to market after round",
                prices=self.db.get_all_stock_prices(),
            )
            self.write_trade_log(settlement_rows, new_prices)
            logger.info("Market settled. Prices updated with daily price limits.")
            self._set_round_metrics(
                total_jobs=len(market_jobs),
                successful_responses=len(market_jobs) - market_fallback_count,
                fallback_count=market_fallback_count,
                valid_orders=len(lottery_barrel),
                matched_trades=executed_settlements,
            )
            self._write_status(
                status="idle",
                message="Market round complete",
                extra={
                    "round_type": "market",
                    "symbols_updated": list(new_prices.keys()),
                    "matched_volume": traded_volume_by_symbol,
                    "stock_splits": [],
                },
            )
        else:
            current_prices = self.db.get_all_stock_prices()
            for symbol, price in current_prices.items():
                self.db.record_price(
                    symbol,
                    price,
                    volume=0,
                    tick=self.clock.current_tick,
                    day=self.clock.day_count,
                    day_tick=self.clock.day_tick,
                    phase=self._current_phase_name(),
                )
                self.db.record_market_round_stat(
                    tick=self.clock.current_tick,
                    day=self.clock.day_count,
                    day_tick=self.clock.day_tick,
                    phase=self._current_phase_name(),
                    symbol=symbol,
                    buy_volume=0,
                    sell_volume=0,
                    matched_volume=0,
                    open_price=float(self.engine.stock_config[symbol]["price"]),
                    close_price=price,
                )
            self._set_round_metrics(
                total_jobs=len(market_jobs),
                successful_responses=len(market_jobs) - market_fallback_count,
                fallback_count=market_fallback_count,
                valid_orders=len(lottery_barrel),
                matched_trades=0,
            )
            self._write_status(
                status="idle",
                message="Market round complete with no valid orders",
                extra={"round_type": "market", "stock_splits": []},
            )

    def run_social_round(self):
        logger.info("%s | Socializing phase...", datetime.now().strftime("%H:%M"))
        self._load_agent_traits()
        self._write_status(status="running", message="Social round started", extra={"round_type": "social"})
        with self.db._get_connection() as conn:
            agents = conn.execute("SELECT id, role FROM agents WHERE is_active = 1").fetchall()
        policy_notice = self._latest_policy_notice()
        bulletin_notice = self._latest_bulletin_notice()
        recent_fundamental_events = self.db.get_recent_fundamental_events(limit=8)

        social_jobs: List[Dict[str, Any]] = []
        social_job_meta: List[Dict[str, Any]] = []
        for agent in agents:
            agent_id, role = agent["id"], agent["role"]
            state = self.db.get_agent_state(agent_id)
            traits = self.agent_traits.get(agent_id, "Standard investor")
            trust_profile = self._fundamental_event_trust_profile(role)
            memory_context = self._build_memory_context(state, mode="social")
            wealth_context = f"Your Current Wealth: ${state['cash']:.2f} and holdings {state['holdings']}.\n"
            consistency_context = self._build_position_consistency_context(state)
            market_context = self._get_market_context_for_agent(agent_id, role, state)
            feed = self.forum.get_feed(agent_id)
            feed_text = "\n".join([f"PostID {post.post_id} by {post.agent_id}: {post.content}" for post in feed])

            prompt = (
                f"Your Traits: {traits}\n"
                f"Simulation Time: {self.clock.get_virtual_time()}\n"
                f"Simulation Tick: {self.clock.current_tick} | Day Tick: {self.clock.day_tick} | Phase: {self._current_phase_name()}\n"
                f"--- Your Memories ---\n{memory_context}\n"
                f"Latest Policy Notice: {policy_notice}\n"
                f"Latest Bulletin Board Notice: {bulletin_notice}\n"
                f"{wealth_context}{consistency_context}\n\n{market_context}\n\nFeed:\n{feed_text}\n\n"
                f"Instructions: You are in the SOCIAL PHASE. You cannot buy or sell stocks now.\n"
                f"Treat policy, forum, and price action as competing signals when deciding what narrative to amplify or resist.\n"
                f"Let fundamentals matter too: expensive momentum, cheap quality, dividend support, and liquidation risk should shape your story.\n"
                f"Your fundamental-event trust profile is {trust_profile['reaction_bias']} with trust {trust_profile['trust']:.2f}; "
                f"do not talk about company events as if you trust them more than your own profile would allow.\n"
                f"Keep your public posts aligned with your current positioning and recent trading stance unless you are explicitly announcing a change of mind.\n"
                f"Analyze the feed and decide if you want to share a thought, react to others, or stay silent.\n"
                f"Let your relationship memory and long-term beliefs shape whether you trust, mock, follow, or ignore other agents.\n\n"
                f'Return JSON: {{"action": "post"|"reply"|"hold", "content": "...", "target_id": ..., "thought_process": "..."}}'
            )
            social_jobs.append(
                {
                    "agent_id": agent_id,
                    "role": role,
                    "mode": "social",
                    "prompt": prompt,
                }
            )
            social_job_meta.append({"agent_id": agent_id, "role": role, "feed_text": feed_text})

        social_decisions = self._run_llm_jobs(social_jobs)
        social_fallback_count = 0
        for meta, decision in zip(social_job_meta, social_decisions):
            agent_id = meta["agent_id"]
            role = meta["role"]
            feed_text = meta["feed_text"]
            if self._is_fallback_decision(decision):
                social_fallback_count += 1
            logger.info("[Social] %s (%s) -> %s | %s", agent_id, role, str(decision.get("action") or "UNKNOWN").upper(), decision.get("thought_process"))
            self._record_social_memory(agent_id, feed_text, decision)

            if decision.get("action") == "post":
                content = self._safe_post_content(decision.get("content"))
                sentiment = random.uniform(-1, 1)
                is_hot = self._should_mark_hot_post(
                    agent_id=agent_id,
                    role=role,
                    content=content,
                    sentiment=sentiment,
                    action="post",
                )
                self.forum.add_post(agent_id, content, sentiment, is_hot=is_hot)
            elif decision.get("action") == "reply":
                content = self._safe_post_content(decision.get("content"))
                sentiment = random.uniform(-1, 1)
                self.forum.add_post(
                    agent_id,
                    content,
                    sentiment,
                    parent_id=self._safe_parent_id(decision.get("target_id")),
                )

        self._record_all_asset_snapshots(
            source="social_round",
            note="Social phase snapshot",
            prices=self.db.get_all_stock_prices(),
        )
        self._set_round_metrics(
            total_jobs=len(social_jobs),
            successful_responses=len(social_jobs) - social_fallback_count,
            fallback_count=social_fallback_count,
            valid_orders=0,
            matched_trades=0,
        )
        self._write_status(status="idle", message="Social round complete", extra={"round_type": "social"})

    def run_reflection_round(self):
        logger.info("%s | Reflection phase: agents are reviewing their day...", datetime.now().strftime("%H:%M"))
        self._load_agent_traits()
        self._write_status(status="running", message="Reflection round started", extra={"round_type": "reflection"})
        with self.db._get_connection() as conn:
            agents = conn.execute("SELECT id, role FROM agents WHERE is_active = 1").fetchall()
        policy_notice = self._latest_policy_notice()
        bulletin_notice = self._latest_bulletin_notice()
        recent_fundamental_events = self.db.get_recent_fundamental_events(limit=8)

        reflection_jobs: List[Dict[str, Any]] = []
        reflection_job_meta: List[Dict[str, Any]] = []
        for agent in agents:
            agent_id, role = agent["id"], agent["role"]
            state = self.db.get_agent_state(agent_id)
            trading_memory = self._get_json_field(state, "trading_memory", [])
            social_memory = self._get_json_field(state, "social_memory", [])
            policy_memory = self._get_json_field(state, "policy_memory", [])
            bulletin_memory = self._get_json_field(state, "bulletin_memory", [])
            fundamental_memory = self._get_json_field(state, "fundamental_memory", [])
            episodic_summary = self._get_json_field(state, "episodic_summary", {})
            belief_memory = self._get_json_field(state, "belief_memory", {})
            relationship_memory = self._get_json_field(state, "relationship_memory", {})
            trust_profile = self._fundamental_event_trust_profile(role)
            compact_trades = self._compact_reflection_records(trading_memory, limit=3)
            compact_social = self._compact_reflection_records(social_memory, limit=3)
            compact_bulletins = self._compact_reflection_records(bulletin_memory, limit=3)
            compact_fundamentals = self._compact_reflection_records(fundamental_memory, limit=3)
            compact_recent_fundamentals = self._compact_reflection_records(recent_fundamental_events, limit=3)
            compact_episodic = self._compact_episodic_summary(episodic_summary)
            compact_beliefs = self._compact_key_value_memory(belief_memory, limit=3, value_limit=90)
            compact_relationships = self._compact_key_value_memory(relationship_memory, limit=3, value_limit=90)

            prompt = (
                "You are now in the reflection phase at the end of the simulated day.\n"
                "Be concise. Keep all strings short and factual.\n"
                f"Simulation Time: {self.clock.get_virtual_time()}\n"
                f"Simulation Tick: {self.clock.current_tick} | Day Tick: {self.clock.day_tick} | Phase: {self._current_phase_name()}\n"
                f"Role: {role}\n\n"
                f"Recent Trading History: {compact_trades}\n"
                f"Recent Social Experiences: {compact_social}\n"
                f"Current Bulletin Memory: {compact_bulletins}\n"
                f"Current Fundamental Event Memory: {compact_fundamentals}\n"
                f"Current Episodic Summary: {compact_episodic}\n"
                f"Current Core Beliefs: {compact_beliefs}\n"
                f"Current Relationship Memory: {compact_relationships}\n\n"
                f"Latest Policy Notice: {policy_notice}\n"
                f"Latest Bulletin Board Notice: {bulletin_notice}\n\n"
                f"Recent Fundamental Events: {compact_recent_fundamentals}\n\n"
                "Analyze your day and update only the most important memory changes.\n"
                "Preserve continuity, but keep outputs compact.\n"
                "Only promote information into long-term beliefs when it reflects repeated behavior, repeated market evidence, or meaningful policy/personal impact.\n"
                "One-off noise should stay in the episodic summary, not long-term beliefs.\n"
                "For bulletin and fundamental memory, keep only still-relevant items and drop stale ones.\n\n"
                f"Your fundamental-event trust profile is {trust_profile['reaction_bias']} with trust {trust_profile['trust']:.2f}.\n"
                "For fundamental memory, keep only the items that still matter to your worldview.\n\n"
                "Constraints:\n"
                "- belief_memory: at most 3 keys\n"
                "- relationship_memory: at most 3 keys\n"
                "- bulletin_memory: at most 3 items\n"
                "- fundamental_memory: at most 3 items\n"
                "- each summary string should be under 20 words\n"
                "- reflection_summary should be one short sentence\n\n"
                "Return JSON with this exact shape:\n"
                '{"belief_memory": {...}, "episodic_summary": {...}, "relationship_memory": {...}, "bulletin_memory": [...], "fundamental_memory": [...], "reflection_summary": "..."}'
            )
            reflection_jobs.append(
                {
                    "agent_id": agent_id,
                    "role": role,
                    "mode": "reflection",
                    "prompt": prompt,
                }
            )
            reflection_job_meta.append(
                {
                    "agent_id": agent_id,
                    "role": role,
                    "trading_memory": trading_memory,
                    "social_memory": social_memory,
                    "policy_memory": policy_memory,
                    "bulletin_memory": bulletin_memory,
                    "fundamental_memory": fundamental_memory,
                    "episodic_summary": episodic_summary,
                    "belief_memory": belief_memory,
                    "relationship_memory": relationship_memory,
                }
            )

        reflection_decisions = self._run_llm_jobs(reflection_jobs)
        reflection_fallback_count = 0
        for meta, decision in zip(reflection_job_meta, reflection_decisions):
            agent_id = meta["agent_id"]
            role = meta["role"]
            trading_memory = meta["trading_memory"]
            social_memory = meta["social_memory"]
            policy_memory = meta["policy_memory"]
            bulletin_memory = meta["bulletin_memory"]
            fundamental_memory = meta["fundamental_memory"]
            episodic_summary = meta["episodic_summary"]
            belief_memory = meta["belief_memory"]
            relationship_memory = meta["relationship_memory"]
            if self._is_fallback_decision(decision):
                reflection_fallback_count += 1
            logger.info("[Reflection] %s (%s) -> %s", agent_id, role, decision.get("reflection_summary", "No summary provided."))

            stabilized_beliefs = self._stabilize_belief_memory(
                current_beliefs=belief_memory,
                proposed_beliefs=self._as_dict(decision.get("belief_memory")),
                trading_memory=trading_memory,
                social_memory=social_memory,
                policy_memory=policy_memory,
            )
            compressed_summary = self._compress_episodic_summary(
                current_summary=episodic_summary,
                proposed_summary=self._as_dict(decision.get("episodic_summary")),
            )
            stabilized_bulletins = self._stabilize_bulletin_memory(
                current_bulletins=bulletin_memory,
                proposed_bulletins=self._as_list(decision.get("bulletin_memory")),
            )
            stabilized_fundamentals = self._stabilize_fundamental_memory(
                current_items=fundamental_memory,
                proposed_items=self._as_list(decision.get("fundamental_memory")),
            )
            stabilized_relationships = self._stabilize_relationship_memory(
                current_relationships=relationship_memory,
                proposed_relationships=self._as_dict(decision.get("relationship_memory")),
            )

            with self.db._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE agents
                    SET belief_memory = ?, episodic_summary = ?, relationship_memory = ?, bulletin_memory = ?, fundamental_memory = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(stabilized_beliefs, ensure_ascii=False),
                        json.dumps(compressed_summary, ensure_ascii=False),
                        json.dumps(stabilized_relationships, ensure_ascii=False),
                        json.dumps(stabilized_bulletins, ensure_ascii=False),
                        json.dumps(stabilized_fundamentals, ensure_ascii=False),
                        agent_id,
                    ),
                )
        self._set_round_metrics(
            total_jobs=len(reflection_jobs),
            successful_responses=len(reflection_jobs) - reflection_fallback_count,
            fallback_count=reflection_fallback_count,
            valid_orders=0,
            matched_trades=0,
        )
        self._write_status(status="idle", message="Reflection round complete", extra={"round_type": "reflection"})

    def start(self):
        self.setup_world()
        self._repair_account_integrity()
        try:
            while True:
                # One broken round must never kill the whole simulation: the LLM can
                # return anything and the DB can hiccup between two processes.
                sleep_duration = self.round_sleep_seconds_open
                iteration_failed = False
                clock_advanced = False
                try:
                    if self.clock.state == MarketState.OPEN:
                        self.run_market_round()
                        sleep_duration = self.round_sleep_seconds_open
                    else:
                        self.run_social_round()
                        sleep_duration = self.round_sleep_seconds_closed

                    self.clock.tick()
                    clock_advanced = True

                    if self.clock.day_tick == self.clock.TICK_PER_DAY - 1:
                        logger.info("End of day: salaries distributed and costs deducted.")
                        self._write_status(status="running", message="Day close settlement started", extra={"round_type": "day_close"})
                        policy_result = self._apply_central_policy()
                        with self.db._get_connection() as conn:
                            agents = conn.execute("SELECT id, salary, cost_multiplier FROM agents WHERE is_active = 1").fetchall()
                            for agent in agents:
                                net_income = agent["salary"] - (self.base_living_cost * agent["cost_multiplier"])
                                self.db.update_agent_cash(agent["id"], net_income)
                        self._record_all_asset_snapshots(
                            source="day_close",
                            note="Post salary and living cost settlement",
                            prices=self.db.get_all_stock_prices(),
                        )
                        self.run_reflection_round()
                        self._advance_stock_fundamentals()
                        fundamental_events = self._trigger_fundamental_events()
                        split_results = self._auto_split_overpriced_stocks()
                        prices = self.db.get_all_stock_prices()
                        repair_report = self._repair_account_integrity()
                        health_report = self._run_db_health_check(prices=prices)
                        self._write_daily_audit_log(policy_result, health_report, prices)
                        self._write_status(
                            status="idle",
                            message="Day close settlement complete",
                            extra={
                                "round_type": "day_close",
                                "policy_type": policy_result["decision"]["policy_type"],
                                "latest_policy": policy_result["decision"]["summary"],
                                "health_status": health_report["status"],
                                "health_issue_count": len(health_report["issues"]),
                                "integrity_repairs": repair_report,
                                "fundamental_events": fundamental_events,
                                "stock_splits": split_results,
                            },
                        )
                except Exception:
                    iteration_failed = True
                    logger.exception("Simulation iteration failed; recovering and continuing")
                    try:
                        self._write_status(status="error", message="Iteration failed; see simulation log", extra={"round_type": "error"})
                    except Exception:
                        logger.exception("Could not update status after a failed iteration")
                    # Advance the clock anyway so a permanently broken tick cannot wedge the loop.
                    if not clock_advanced:
                        try:
                            self.clock.tick()
                        except Exception:
                            logger.exception("Could not advance the virtual clock")

                self._save_runtime_state()
                logger.info("Sleeping for %ss...", sleep_duration)
                # Keep the error status visible on the dashboard instead of
                # immediately overwriting it with "sleeping".
                if not iteration_failed:
                    self._write_status(status="sleeping", message=f"Sleeping for {sleep_duration}s", extra={"sleep_duration": sleep_duration})
                time.sleep(sleep_duration)
        except KeyboardInterrupt:
            logger.info("Server stopped.")
            self._save_runtime_state()
            self._write_status(status="stopped", message="Server stopped by keyboard interrupt")


if __name__ == "__main__":
    # Priority: Xiaomi key > Gemini key > optional llama.cpp server key.
    # With llama.cpp (LLM_PROVIDER=llama_cpp) no key is needed unless the
    # server was started with --api-key.
    api_key = (
        os.environ.get("MIMO_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("LLAMA_API_KEY", "")
    )
    server = SimulationServer(api_key=api_key, sleep_interval=300)
    server.start()
