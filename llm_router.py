import json
import logging
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

import google.generativeai as genai

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("AgentBrain")

# Per-provider defaults. Any of them can be overridden by the matching
# environment variable (MIMO_*, LLAMA_*) or by an explicit constructor argument.
PROVIDER_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "xiaomi_openai": {
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "model": "mimo-v2.5-pro",
        "timeout_seconds": 30,
    },
    "llama_cpp": {
        # llama-server (llama.cpp) default OpenAI-compatible endpoint.
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "local-model",
        # Local models on CPU can be much slower than a cloud API.
        "timeout_seconds": 300,
    },
    "gemini": {
        "base_url": "",
        "model": "gemini-3.1-flash-lite-preview",
        "timeout_seconds": 30,
    },
}

# Environment variables that override the defaults above.
ENV_OVERRIDES: Dict[str, Dict[str, str]] = {
    "xiaomi_openai": {"base_url": "MIMO_BASE_URL", "model": "MIMO_MODEL"},
    "llama_cpp": {"base_url": "LLAMA_BASE_URL", "model": "LLAMA_MODEL"},
    "gemini": {"base_url": "", "model": "GEMINI_MODEL"},
}
ENV_TIMEOUT = "LLM_TIMEOUT_SECONDS"
ENV_LLAMA_MAX_TOKENS = "LLAMA_MAX_TOKENS"
ENV_LLAMA_THINKING = "LLAMA_THINKING"


MODE_CONFIGS: Dict[str, Dict[str, Any]] = {
    "market": {
        "required_any_keys": {"trades"},
        "max_completion_tokens": 220,
        "temperature": 0.3,
        "max_retries": 2,
        "retry_delay_seconds": 8,
        "example": {
            "trades": [{"action": "buy", "stock": "VDA", "amount": 10}],
            "thought_process": "The current price is attractive and aligns with my conviction.",
        },
        "fallback": {
            "trades": [],
            "thought_process": "System fallback.",
        },
    },
    "social": {
        "required_any_keys": {"action"},
        "max_completion_tokens": 220,
        "temperature": 0.25,
        "max_retries": 1,
        "retry_delay_seconds": 6,
        "example": {
            "action": "post",
            "content": "I still believe this company has long-term upside.",
            "target_id": None,
            "thought_process": "I want to reinforce my current narrative publicly.",
        },
        "fallback": {
            "action": "hold",
            "content": "",
            "target_id": None,
            "thought_process": "System fallback.",
        },
    },
    "reflection": {
        "required_any_keys": {
            "belief_memory",
            "episodic_summary",
            "relationship_memory",
            "bulletin_memory",
            "fundamental_memory",
            "reflection_summary",
        },
        "max_completion_tokens": 520,
        "temperature": 0.2,
        "max_retries": 1,
        "retry_delay_seconds": 10,
        "example": {
            "belief_memory": {"SLA": "Speculative but still attractive."},
            "episodic_summary": {
                "recent_market_story": "SLA stayed central to market attention.",
                "recent_behavior_pattern": "I traded with slightly higher concentration.",
                "recent_lesson": "Momentum helped, but risk rose too quickly.",
            },
            "relationship_memory": {
                "Agent_009": "Still amplifies risky hype."
            },
            "bulletin_memory": [
                {
                    "bulletin_id": 12,
                    "title": "Factory outage rumor",
                    "summary": "Still matters for SMC sentiment.",
                    "importance": 0.62,
                    "retain": True
                }
            ],
            "fundamental_memory": [
                {
                    "event_id": 8,
                    "symbol": "VDA",
                    "event_type": "earnings_beat",
                    "summary": "VDA beat earnings and still supports value.",
                    "trust": 0.82,
                    "importance": 0.74,
                    "stance": "bullish",
                    "retain": True,
                }
            ],
            "reflection_summary": "I stayed constructive but became more careful about momentum risk.",
        },
        "fallback": {
            "belief_memory": {},
            "episodic_summary": {},
            "relationship_memory": {},
            "bulletin_memory": [],
            "fundamental_memory": [],
            "reflection_summary": "System fallback.",
        },
    },
}


class AgentBrain:
    def __init__(
        self,
        api_key: str = "",
        model_name: Optional[str] = None,
        provider: str = "auto",
        base_url: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ):
        self.api_key = api_key or ""
        self.provider = self._resolve_provider(
            provider=provider, api_key=self.api_key, model_name=model_name, base_url=base_url
        )

        defaults = PROVIDER_DEFAULTS.get(self.provider, PROVIDER_DEFAULTS["gemini"])
        env_names = ENV_OVERRIDES.get(self.provider, {})

        env_base_url = os.environ.get(env_names.get("base_url", ""), "").strip()
        env_model = os.environ.get(env_names.get("model", ""), "").strip()
        self.base_url = (base_url or env_base_url or defaults["base_url"]).rstrip("/")
        self.model_name = model_name or env_model or defaults["model"]
        self.timeout_seconds = self._resolve_timeout(timeout_seconds, int(defaults["timeout_seconds"]))
        self.model = None

        if self.provider == "gemini":
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(
                model_name=self.model_name,
                generation_config={"response_mime_type": "application/json"},
            )
        elif self.provider == "llama_cpp":
            # llama-server is plain HTTP, nothing to initialise here.
            logger.info("llama.cpp endpoint: %s/chat/completions", self.base_url)

        logger.info("AgentBrain provider=%s model=%s", self.provider, self.model_name)

        self.role_prompts = {
            "Boss": "You are a wealthy venture capitalist. You prioritize long-term value, high-dividend stocks, and aggressive expansion. You have a high risk tolerance but hate losing face.",
            "Journalist": "You are a financial investigative journalist. You look for anomalies, scandals, and insider tips. Your goal is to find the truth before others do.",
            "MiddleClass": "You are a cautious middle-class investor. You prioritize stability and diversification. You are terrified of losing your savings and follow mainstream trends.",
            "RetailInvestor": "You are a degenerate retail trader. You love memes, high volatility, and going to the moon. You often trade based on emotions and social media hype.",
        }

    @staticmethod
    def _resolve_timeout(explicit: Optional[int], default: int) -> int:
        if explicit is not None:
            return max(1, int(explicit))
        raw = os.environ.get(ENV_TIMEOUT, "").strip()
        if raw:
            try:
                return max(1, int(raw))
            except ValueError:
                logger.warning("Ignoring invalid %s=%r", ENV_TIMEOUT, raw)
        return default

    @staticmethod
    def _is_loopback_url(url: str) -> bool:
        """True for a local endpoint such as http://127.0.0.1:8080/v1."""
        try:
            host = (urllib.parse.urlparse(url).hostname or "").lower()
        except ValueError:
            return False
        return host in ("127.0.0.1", "localhost", "::1")

    def _resolve_provider(self, provider: str, api_key: str, model_name: Optional[str], base_url: Optional[str]) -> str:
        provider = (provider or "auto").strip().lower()
        if provider == "auto":
            if (api_key or "").startswith("tp-") or (model_name or "").startswith("mimo-") or (base_url and "xiaomimimo.com" in base_url):
                return "xiaomi_openai"
            # A loopback endpoint is a local llama.cpp server, not a cloud API.
            if base_url and self._is_loopback_url(base_url):
                return "llama_cpp"
            if base_url and not self._is_loopback_url(base_url) and "gemini" not in (model_name or ""):
                # Unknown remote base_url: treat it as an OpenAI-compatible server.
                return "xiaomi_openai"
            return "gemini"
        if provider not in PROVIDER_DEFAULTS:
            raise ValueError(
                f"Unknown LLM provider '{provider}'. Expected one of: auto, {', '.join(sorted(PROVIDER_DEFAULTS))}."
            )
        return provider

    def validate_json(self, raw_response: str, mode: str) -> Optional[Dict[str, Any]]:
        try:
            cleaned_res = raw_response.strip()
            if cleaned_res.startswith("```"):
                cleaned_res = cleaned_res.split("```")[1]
                if cleaned_res.startswith("json"):
                    cleaned_res = cleaned_res[4:]

            data = json.loads(cleaned_res)
            if not isinstance(data, dict):
                return None

            required_any_keys = MODE_CONFIGS[mode]["required_any_keys"]
            if not any(key in data for key in required_any_keys):
                return None

            # Presence of a key is not enough: enforce the shape so downstream
            # code never has to defend against a string where a list was expected.
            return self.normalize_response(mode, data)
        except Exception as exc:
            logger.warning("JSON Validation Failed (%s): %s", mode, exc)
            return None

    @staticmethod
    def _as_text(value: Any, limit: int) -> str:
        if not isinstance(value, str):
            return ""
        return value.strip()[:limit]

    @staticmethod
    def _as_number(value: Any, default: float, low: float, high: float) -> float:
        if isinstance(value, bool) or value is None:
            return default
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return max(low, min(high, number))

    @staticmethod
    def _as_mapping(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _as_sequence(value: Any) -> list:
        return value if isinstance(value, list) else []

    def normalize_response(self, mode: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Coerce a parsed model response into the exact shape each mode promises."""
        if mode == "market":
            raw_trades = data.get("trades")
            if not isinstance(raw_trades, list):
                return None
            trades = []
            for item in raw_trades:
                if not isinstance(item, dict):
                    continue
                action = str(item.get("action", "hold")).strip().lower()
                if action not in ("buy", "sell", "hold"):
                    action = "hold"
                amount = item.get("amount", 0)
                if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                    continue
                trades.append(
                    {
                        "action": action,
                        "stock": str(item.get("stock", "")),
                        "amount": int(amount),
                    }
                )
            return {
                "trades": trades,
                "thought_process": self._as_text(data.get("thought_process"), 400),
            }

        if mode == "social":
            action = str(data.get("action", "hold")).strip().lower()
            if action not in ("post", "reply", "hold"):
                action = "hold"
            content = self._as_text(data.get("content"), 600)
            if action in ("post", "reply") and not content:
                action = "hold"
            target_id = data.get("target_id")
            if isinstance(target_id, str) and target_id.isdigit():
                target_id = int(target_id)
            elif isinstance(target_id, bool) or not isinstance(target_id, (int, type(None))):
                target_id = None
            return {
                "action": action,
                "content": content,
                "target_id": target_id,
                "thought_process": self._as_text(data.get("thought_process"), 400),
            }

        if mode == "reflection":
            bulletin_memory = []
            for item in self._as_sequence(data.get("bulletin_memory")):
                if not isinstance(item, dict):
                    continue
                item = dict(item)
                item["importance"] = self._as_number(item.get("importance"), 0.5, 0.0, 1.0)
                item["title"] = self._as_text(item.get("title"), 160)
                item["summary"] = self._as_text(item.get("summary") or item.get("content"), 300)
                bulletin_memory.append(item)

            fundamental_memory = []
            for item in self._as_sequence(data.get("fundamental_memory")):
                if not isinstance(item, dict):
                    continue
                item = dict(item)
                item["importance"] = self._as_number(item.get("importance"), 0.5, 0.0, 1.0)
                item["trust"] = self._as_number(item.get("trust"), 0.5, 0.0, 1.0)
                item["summary"] = self._as_text(item.get("summary"), 300)
                fundamental_memory.append(item)

            return {
                "belief_memory": self._as_mapping(data.get("belief_memory")),
                "episodic_summary": self._as_mapping(data.get("episodic_summary")),
                "relationship_memory": self._as_mapping(data.get("relationship_memory")),
                "bulletin_memory": bulletin_memory,
                "fundamental_memory": fundamental_memory,
                "reflection_summary": self._as_text(data.get("reflection_summary"), 400),
            }

        return data

    def _build_user_prompt(self, prompt: str, example_json: Dict[str, Any], mode: str) -> str:
        extra_instruction = ""
        if mode == "social":
            extra_instruction = (
                "Keep every string to one line. Keep content under 80 words. "
                "Do not include double quote characters inside any string value.\n"
            )
        elif mode == "reflection":
            extra_instruction = (
                "Keep every string concise. Limit belief_memory to 3 keys, relationship_memory to 3 keys, "
                "bulletin_memory to 3 items, and fundamental_memory to 3 items. "
                "Do not include double quote characters inside any string value.\n"
            )
        elif mode == "market":
            extra_instruction = (
                "Keep thought_process to one short sentence. Do not include double quote characters inside any string value.\n"
            )
        return (
            f"{prompt}\n\n"
            f"IMPORTANT: You must respond ONLY with a valid JSON object.\n"
            f"{extra_instruction}"
            f"Use this exact response style as a guide:\n{json.dumps(example_json, ensure_ascii=False)}\n"
        )

    def _generate_gemini(self, system_instruction: str, user_prompt: str) -> str:
        full_prompt = f"{system_instruction}\n\n{user_prompt}"
        try:
            response = self.model.generate_content(
                full_prompt,
                request_options={"timeout": self.timeout_seconds},
            )
        except TypeError:
            # Older google-generativeai builds do not accept request_options.
            response = self.model.generate_content(full_prompt)
        return response.text

    def _generate_openai(
        self,
        system_instruction: str,
        user_prompt: str,
        *,
        max_completion_tokens: int,
        temperature: float,
    ) -> str:
        """POST to an OpenAI-compatible /chat/completions endpoint.

        Used by the Xiaomi gateway and by llama.cpp (llama-server). The two
        disagree on a few field names, so the payload is built per provider:
        sending llama-server `response_format` / `thinking` makes it reject the
        request, and sending the gateway `max_tokens` does the same.
        """
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "top_p": 0.95,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}

        if self.provider == "xiaomi_openai":
            payload["max_completion_tokens"] = max_completion_tokens
            payload["response_format"] = {"type": "json_object"}
            payload["thinking"] = {"type": "disabled"}
            headers["api-key"] = self.api_key
        else:
            payload["max_tokens"], extra_fields = self._llama_request_fields(max_completion_tokens)
            payload.update(extra_fields)
            if self.api_key:
                # llama-server only checks this when started with --api-key.
                headers["Authorization"] = f"Bearer {self.api_key}"

        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            body = response.read().decode("utf-8")
        parsed = json.loads(body)
        choices = parsed.get("choices")
        if not choices:
            raise ValueError(f"LLM returned no choices: {str(parsed)[:200]}")
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, list):  # some servers return content parts
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str) or not content.strip():
            finish = choices[0].get("finish_reason")
            if choices[0].get("message", {}).get("reasoning_content"):
                raise ValueError(
                    f"LLM returned an empty message (finish_reason={finish!r}) because its whole "
                    f"token budget went to reasoning; raise {ENV_LLAMA_MAX_TOKENS} or turn thinking "
                    f"off with {ENV_LLAMA_THINKING}=0."
                )
            raise ValueError(f"LLM returned an empty message (finish_reason={finish!r}): {str(parsed)[:200]}")
        return content

    @staticmethod
    def _llama_request_fields(mode_tokens: int):
        """Build the llama.cpp-specific parts of the payload.

        Returns ``(max_tokens, extra_fields)``.

        Reasoning models (Qwen3 and friends) spend ``reasoning_content`` out of
        the *same* ``max_tokens`` budget as the answer. The cloud-sized budget
        of 220 tokens was therefore consumed entirely by thinking and every
        reply came back empty. So the budget is scaled up, and thinking is
        switched off through the chat template (Qwen-style templates honour
        ``enable_thinking``) unless the user opts back in.
        """
        budget = max(int(mode_tokens) * 4, 900)
        raw = os.environ.get(ENV_LLAMA_MAX_TOKENS, "").strip()
        if raw:
            try:
                budget = max(1, int(raw))
            except ValueError:
                logger.warning("Ignoring invalid %s=%r", ENV_LLAMA_MAX_TOKENS, raw)

        thinking = os.environ.get(ENV_LLAMA_THINKING, "").strip().lower()
        if thinking in ("none", "off"):
            # Escape hatch for servers that reject unknown payload fields.
            return budget, {}
        enabled = thinking in ("1", "true", "yes", "on")
        return budget, {"chat_template_kwargs": {"enable_thinking": enabled}}

    def think(self, agent_id: str, role: str, prompt: str, mode: str = "market") -> Dict[str, Any]:
        if mode not in MODE_CONFIGS:
            raise ValueError(f"Unsupported AgentBrain mode: {mode}")

        system_instruction = self.role_prompts.get(role, "You are a generic investor.")
        mode_config = MODE_CONFIGS[mode]
        example_json = mode_config["example"]
        user_prompt = self._build_user_prompt(prompt, example_json, mode)

        max_retries = int(mode_config.get("max_retries", 2))
        retry_delay_seconds = float(mode_config.get("retry_delay_seconds", 3))
        for attempt in range(max_retries + 1):
            try:
                if self.provider in ("xiaomi_openai", "llama_cpp"):
                    raw_text = self._generate_openai(
                        system_instruction,
                        user_prompt,
                        max_completion_tokens=int(mode_config.get("max_completion_tokens", 2048)),
                        temperature=float(mode_config.get("temperature", 0.7)),
                    )
                else:
                    raw_text = self._generate_gemini(system_instruction, user_prompt)
                validated = self.validate_json(raw_text, mode=mode)
                if validated:
                    return validated
            except urllib.error.HTTPError as exc:
                try:
                    error_body = exc.read().decode("utf-8", errors="ignore")
                except Exception:
                    error_body = ""
                logger.warning("API HTTP Error on %s (%s): %s %s", agent_id, mode, exc.code, error_body)
            except Exception as exc:
                logger.warning("API Error on %s (%s): %s", agent_id, mode, exc)

            if attempt < max_retries:
                logger.info("Retrying %s (%s)... (%s/%s)", agent_id, mode, attempt + 1, max_retries)
                # Linear backoff with jitter so parallel workers do not retry in lockstep.
                time.sleep(retry_delay_seconds * (attempt + 1) + random.uniform(0, retry_delay_seconds))

        fallback = dict(mode_config["fallback"])
        if mode == "reflection":
            fallback["belief_memory"] = {}
            fallback["episodic_summary"] = {}
            fallback["relationship_memory"] = {}
            fallback["bulletin_memory"] = []
            fallback["fundamental_memory"] = []
        return fallback
