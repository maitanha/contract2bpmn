"""Thin vLLM wrapper: chat generation with optional JSON-schema constrained decoding."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

DEFAULT_MODEL = os.environ.get("TG_MODEL", "Qwen/Qwen3.5-0.8B")


@dataclass
class GenConfig:
    temperature: float = 0.0
    max_tokens: int = 2048
    seed: int = 13
    n: int = 1


class LLM:
    def __init__(self, model: str = DEFAULT_MODEL, gpu_memory_utilization: float = 0.35, max_model_len: int = 8192):
        from vllm import LLM as _V
        self.model_name = model
        # compact JSON: without this, small models can fill the token budget with whitespace inside the grammar
        self.v = _V(model=model, gpu_memory_utilization=gpu_memory_utilization, max_model_len=max_model_len,
                    seed=13, enforce_eager=False, structured_outputs_config={"backend": "xgrammar", "disable_any_whitespace": True})

    def _params(self, cfg: GenConfig, schema: dict | None):
        from vllm import SamplingParams
        kw = dict(temperature=cfg.temperature, max_tokens=cfg.max_tokens, seed=cfg.seed, n=cfg.n)
        if schema is not None:
            try:  # vLLM >= 0.10
                from vllm.sampling_params import StructuredOutputsParams
                kw["structured_outputs"] = StructuredOutputsParams(json=schema)
            except ImportError:  # older vLLM
                from vllm.sampling_params import GuidedDecodingParams
                kw["guided_decoding"] = GuidedDecodingParams(json=schema)
        return SamplingParams(**kw)

    def chat(self, prompts: list[str], schema: dict | list[dict] | None = None, cfg: GenConfig | None = None,
             system: str | None = None) -> list[list[str]]:
        """Return n completions per prompt. `schema` may be one schema or one per prompt."""
        cfg = cfg or GenConfig()
        msgs = [([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": p}]
                for p in prompts]
        if isinstance(schema, list):
            params = [self._params(cfg, s) for s in schema]
        else:
            params = self._params(cfg, schema)
        outs = self.v.chat(msgs, params, use_tqdm=False, chat_template_kwargs={"enable_thinking": False})
        return [[c.text for c in o.outputs] for o in outs]


def parse_json(text: str) -> dict | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except json.JSONDecodeError:
                return None
        return None


class LlamaServerLLM:
    """Same interface as LLM, backed by a llama.cpp `llama-server` (OpenAI-compatible API).

    Used by the desktop app (CPU, GGUF model) and usable for experiments without vLLM.
    JSON schemas are enforced by llama.cpp grammar-constrained decoding (`response_format`).
    """

    def __init__(self, base_url: str = "http://127.0.0.1:18761", model_name: str = "Qwen3.5-0.8B-Q4_K_M",
                 timeout: float = 600.0, parallel: int = 2):
        self.base_url, self.model_name, self.timeout, self.parallel = base_url.rstrip("/"), model_name, timeout, parallel

    def _one(self, prompt: str, schema: dict | None, cfg: GenConfig, system: str | None) -> list[str]:
        import urllib.request
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body = {"messages": msgs, "temperature": cfg.temperature, "max_tokens": cfg.max_tokens, "seed": cfg.seed,
                "n": 1, "chat_template_kwargs": {"enable_thinking": False}}
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "output", "schema": schema}}
        outs = []
        for k in range(cfg.n):
            body["seed"] = cfg.seed + k
            req = urllib.request.Request(f"{self.base_url}/v1/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                outs.append(json.loads(r.read())["choices"][0]["message"]["content"])
        return outs

    def chat(self, prompts: list[str], schema: dict | list[dict] | None = None, cfg: GenConfig | None = None,
             system: str | None = None) -> list[list[str]]:
        from concurrent.futures import ThreadPoolExecutor
        cfg = cfg or GenConfig()
        schemas = schema if isinstance(schema, list) else [schema] * len(prompts)
        with ThreadPoolExecutor(max_workers=self.parallel) as ex:
            return list(ex.map(lambda ps: self._one(ps[0], ps[1], cfg, system), zip(prompts, schemas)))
