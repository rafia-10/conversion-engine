"""
langfuse_client.py — Thin wrapper around Langfuse v4 for pipeline observability.

Emits per-module latency and segment outcome as a single trace per prospect.
Falls back gracefully (local JSONL only) if Langfuse is not configured.
"""
import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_LOCAL_TRACE_LOG = Path(os.getenv("LANGFUSE_LOCAL_LOG", "outputs/langfuse_traces.jsonl"))


class _SpanRecorder:
    def __init__(self, trace: "PipelineTrace", name: str):
        self._trace = trace
        self._name = name
        self._t0 = time.time()
        self._metadata: Dict[str, Any] = {}

    def set_metadata(self, **kwargs):
        self._metadata.update(kwargs)

    def end(self, output: Any = None):
        latency_ms = int((time.time() - self._t0) * 1000)
        self._trace._record_span(self._name, latency_ms, output, self._metadata)
        return latency_ms


class PipelineTrace:
    def __init__(self, trace_id: str, company: str, contact_email: str):
        self.trace_id = trace_id
        self.company = company
        self.contact_email = contact_email
        self._t0 = time.time()
        self._spans: list[Dict] = []
        self._output: Dict = {}

        self._lf = None
        self._root_cm = None   # context manager kept open for the life of the trace
        self._root_obs = None  # observation object returned by __enter__

        try:
            from langfuse import Langfuse
            secret = os.getenv("LANGFUSE_SECRET_KEY")
            public = os.getenv("LANGFUSE_PUBLIC_KEY")
            base_url = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
            if secret and public:
                self._lf = Langfuse(
                    secret_key=secret,
                    public_key=public,
                    host=base_url,
                )
                self._root_cm = self._lf.start_as_current_observation(
                    name=f"conversion-engine/{company}",
                    metadata={"company": company, "contact_email": contact_email},
                )
                self._root_obs = self._root_cm.__enter__()
        except Exception as e:
            logger.debug(f"Langfuse init skipped: {e}")

    @contextmanager
    def span(self, name: str):
        rec = _SpanRecorder(self, name)
        if self._lf and self._root_obs:
            try:
                with self._lf.start_as_current_observation(name=name) as lf_span:
                    yield rec
                    latency_ms = rec.end()
                    lf_span.update(metadata={**rec._metadata, "latency_ms": latency_ms})
                return
            except Exception as e:
                logger.debug(f"Langfuse span error: {e}")
        # fallback — no Langfuse
        try:
            yield rec
        finally:
            rec.end()

    def _record_span(self, name: str, latency_ms: int, output: Any, metadata: Dict):
        self._spans.append({"name": name, "latency_ms": latency_ms, "metadata": metadata})

    def finish(self, segment: str, confidence: float, ai_maturity_score: int,
               send_status: str, total_tokens: int = 0, cost_usd: float = 0.0):
        total_ms = int((time.time() - self._t0) * 1000)
        self._output = {
            "segment": segment,
            "confidence": confidence,
            "ai_maturity_score": ai_maturity_score,
            "send_status": send_status,
            "total_tokens": total_tokens,
            "cost_usd": round(cost_usd, 6),
            "total_latency_ms": total_ms,
        }

        # Write local JSONL
        _LOCAL_TRACE_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "trace_id": self.trace_id,
            "company": self.company,
            "contact_email": self.contact_email,
            "spans": self._spans,
            "output": self._output,
        }
        try:
            with _LOCAL_TRACE_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write local trace: {e}")

        # Flush Langfuse — update root observation then close context
        if self._lf and self._root_cm and self._root_obs:
            try:
                self._root_obs.update(
                    output=json.dumps(self._output),
                    metadata={
                        "segment": segment,
                        "ai_maturity": ai_maturity_score,
                        "total_latency_ms": total_ms,
                        "cost_usd": cost_usd,
                    },
                )
            except Exception as e:
                logger.debug(f"Langfuse root update error: {e}")
            try:
                self._root_cm.__exit__(None, None, None)
            except Exception:
                pass
            try:
                self._lf.flush()
            except Exception as e:
                logger.debug(f"Langfuse flush error: {e}")

        return record


class TracingClient:
    _instance: Optional["TracingClient"] = None

    def new_trace(self, company: str, contact_email: str) -> PipelineTrace:
        import hashlib
        trace_id = hashlib.sha256(
            f"{company}:{contact_email}:{time.time()}".encode()
        ).hexdigest()[:16]
        return PipelineTrace(trace_id, company, contact_email)

    @classmethod
    def get(cls) -> "TracingClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


tracing = TracingClient.get()
