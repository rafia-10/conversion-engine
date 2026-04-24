"""
langfuse_client.py — Thin wrapper around Langfuse for pipeline observability.

Emits per-module latency, token cost, and segment outcome as a single trace
per prospect processed. Falls back gracefully if Langfuse is not configured.
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
    """Records a single span (module) within a trace."""

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
    """Single prospect pipeline trace."""

    def __init__(self, trace_id: str, company: str, contact_email: str):
        self.trace_id = trace_id
        self.company = company
        self.contact_email = contact_email
        self._t0 = time.time()
        self._spans: list[Dict] = []
        self._output: Dict = {}
        self._lf_trace = None

        # Try to initialise Langfuse
        try:
            from langfuse import Langfuse
            secret = os.getenv("LANGFUSE_SECRET_KEY")
            public = os.getenv("LANGFUSE_PUBLIC_KEY")
            base_url = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
            if secret and public:
                lf = Langfuse(
                    secret_key=secret,
                    public_key=public,
                    host=base_url,
                )
                self._lf_trace = lf.trace(
                    name=f"conversion-engine/{company}",
                    id=trace_id,
                    metadata={"company": company, "contact_email": contact_email},
                )
        except Exception as e:
            logger.debug(f"Langfuse init skipped: {e}")

    @contextmanager
    def span(self, name: str):
        """Context manager for timing a pipeline module."""
        rec = _SpanRecorder(self, name)
        try:
            yield rec
        finally:
            rec.end()

    def _record_span(self, name: str, latency_ms: int, output: Any, metadata: Dict):
        entry = {
            "name": name,
            "latency_ms": latency_ms,
            "metadata": metadata,
        }
        self._spans.append(entry)

        if self._lf_trace:
            try:
                self._lf_trace.span(
                    name=name,
                    metadata={**metadata, "latency_ms": latency_ms},
                )
            except Exception as e:
                logger.debug(f"Langfuse span error: {e}")

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

        # Flush to local JSONL
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

        # Flush to Langfuse
        if self._lf_trace:
            try:
                self._lf_trace.update(
                    output=json.dumps(self._output),
                    metadata={
                        "segment": segment,
                        "ai_maturity": ai_maturity_score,
                        "total_latency_ms": total_ms,
                        "cost_usd": cost_usd,
                    },
                )
            except Exception as e:
                logger.debug(f"Langfuse trace finish error: {e}")

        return record


class TracingClient:
    """Factory for creating per-prospect traces."""

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


# Module-level singleton
tracing = TracingClient.get()
