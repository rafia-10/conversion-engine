"""
bench_gate.py — Hard constraint: agent cannot commit to capacity the bench_summary doesn't show.

Parses seed/bench_summary.json at startup. Called before every outbound message is finalised.
If a staffing claim can't be verified, rewrites it to route to a human.
"""
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_BENCH_PATHS = [
    "tenacious_sales_data/seed/bench_summary.json",
    "seed/bench_summary.json",
    "../tenacious_sales_data/seed/bench_summary.json",
]

_CLAIM_PATTERNS = [
    (r'\bwe have\b.*?\b(\d+)\b.*?\b(engineer|developer|architect)\b', "staffing_claim"),
    (r'\b(\d+)\b.*?\b(engineer|developer|architect)s?\b.*?\bavailable\b', "availability_claim"),
    (r'\bcan deploy\b.*?\b(\d+)\b', "deploy_claim"),
    (r'\bteam of (\d+)\b', "team_size_claim"),
    (r'\b(\w+) engineers? available\b', "stack_claim"),
    # Explicit stack+count patterns (e.g. "10 Go engineers", "provide 5 Python engineers")
    (r'\b(\d+)\s+(?:go|golang|python|ml|data|infra|frontend|react|fullstack|nestjs)\s+engineers?\b', "stack_count_explicit"),
    (r'\bprovide\s+(?:you with\s+)?(\d+)\b.*?\bengineers?\b', "provide_claim"),
    (r'\bdeploy\s+(\d+)\b.*?\bengineers?\b', "deploy_engineers_claim"),
    (r'\bgo engineers?\b', "stack_go"),
    (r'\bpython engineers?\b', "stack_python"),
    (r'\bml engineers?\b', "stack_ml"),
    (r'\bdata engineers?\b', "stack_data"),
    (r'\binfra engineers?\b', "stack_infra"),
    (r'\bfrontend engineers?\b', "stack_frontend"),
]

_STACK_ALIASES = {
    "go": "go",
    "golang": "go",
    "python": "python",
    "ml": "ml",
    "machine learning": "ml",
    "ai": "ml",
    "data": "data",
    "infra": "infra",
    "infrastructure": "infra",
    "frontend": "frontend",
    "react": "frontend",
    "fullstack": "fullstack_nestjs",
    "nestjs": "fullstack_nestjs",
}


class BenchGate:
    def __init__(self):
        self.bench = self._load_bench()

    def _load_bench(self) -> Dict:
        for path in _BENCH_PATHS:
            p = Path(path)
            if p.exists():
                try:
                    with p.open(encoding="utf-8") as f:
                        return json.load(f)
                except Exception as e:
                    logger.warning(f"Failed to load bench from {path}: {e}")
        logger.error("bench_summary.json not found — all staffing claims will be blocked")
        return {}

    def available(self, stack: str) -> int:
        """Return available engineer count for a stack. 0 if unknown."""
        key = _STACK_ALIASES.get(stack.lower(), stack.lower())
        stacks = self.bench.get("stacks", {})
        return stacks.get(key, {}).get("available_engineers", 0)

    def check_commitment(self, claim_text: str) -> Dict:
        """
        Scan claim_text for staffing commitments. Verify against bench.
        Returns: {"approved": bool, "blocked_claims": List[str], "rewritten": str}
        """
        blocked = []
        text = claim_text

        # Check explicit stack mentions (known aliases)
        for stack_term, stack_key in _STACK_ALIASES.items():
            if re.search(rf'\b{re.escape(stack_term)}\b', text, re.IGNORECASE):
                available = self.available(stack_key)
                if available == 0:
                    blocked.append(
                        f"{stack_term} engineers not on current bench"
                    )
                    text = re.sub(
                        rf'we have \w+ {re.escape(stack_term)} engineers? available',
                        f"let me confirm current {stack_term} team availability with our delivery lead",
                        text, flags=re.IGNORECASE,
                    )

        # Check for "X engineers" claims where X is NOT in our known bench stacks
        known_stacks = set(self.bench.get("stacks", {}).keys()) | set(_STACK_ALIASES.keys())
        unknown_stack_re = re.compile(
            r'\b([A-Za-z][A-Za-z0-9#+\-\.]*)\s+engineers?\b', re.IGNORECASE
        )
        for m in unknown_stack_re.finditer(text):
            tech = m.group(1).lower()
            if tech not in known_stacks and tech not in {"the", "our", "your", "software", "senior", "junior", "mid"}:
                blocked.append(f"{tech} engineers — not a known bench stack; routing to delivery lead")
                text = text.replace(
                    m.group(0),
                    "let me confirm team availability with our delivery lead",
                )

        # Check numeric capacity claims — also cross-reference against stack availability
        # Build stack→count map from context for numeric cross-checking
        stack_in_context = {}
        for stack_term, stack_key in _STACK_ALIASES.items():
            if re.search(rf'\b{re.escape(stack_term)}\b', text, re.IGNORECASE):
                stack_in_context[stack_key] = self.available(stack_key)

        for pattern, claim_type in _CLAIM_PATTERNS:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                if claim_type.startswith("stack_"):
                    continue  # handled above
                raw = m.group(0)
                nums = re.findall(r'\d+', raw)
                if not nums:
                    continue
                claimed = int(nums[0])

                # If stack mentioned in context, compare against stack availability
                if stack_in_context:
                    min_available = min(stack_in_context.values())
                    if claimed > min_available:
                        stack_name = list(stack_in_context.keys())[
                            list(stack_in_context.values()).index(min_available)]
                        blocked.append(
                            f"claimed {claimed} engineers but {stack_name} bench shows {min_available}"
                        )
                        text = text.replace(
                            raw,
                            "let me confirm exact team sizing with our delivery lead",
                        )
                        break
                else:
                    total_bench = self.bench.get("total_engineers_on_bench", 0)
                    if claimed > total_bench:
                        blocked.append(f"claimed {claimed} engineers but bench shows {total_bench}")
                        text = text.replace(
                            raw,
                            "let me confirm exact team sizing with our delivery lead",
                        )
                        break

        return {
            "approved": len(blocked) == 0,
            "blocked_claims": blocked,
            "rewritten": text,
        }

    def get_available_stacks(self) -> List[Dict]:
        """Return list of stacks with available engineers."""
        result = []
        for stack, data in self.bench.get("stacks", {}).items():
            avail = data.get("available_engineers", 0)
            if avail > 0:
                result.append({
                    "stack": stack,
                    "available": avail,
                    "time_to_deploy_days": data.get("time_to_deploy_days", 14),
                    "seniority": data.get("seniority_mix", {}),
                })
        return result

    def match_prospect_needs(self, tech_signals: List[str]) -> Dict:
        """Check if prospect's inferred tech needs match bench availability."""
        matched = []
        missing = []
        for tech in tech_signals:
            key = _STACK_ALIASES.get(tech.lower(), tech.lower())
            avail = self.available(key)
            if avail > 0:
                matched.append({"stack": tech, "available": avail})
            else:
                missing.append(tech)

        return {
            "bench_match": len(missing) == 0,
            "matched_stacks": matched,
            "missing_stacks": missing,
            "recommendation": "proceed" if matched else "route_to_human",
        }
