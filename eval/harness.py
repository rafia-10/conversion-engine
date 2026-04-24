"""
eval/harness.py — τ²-Bench retail domain wrapper.

- Runs 5 trials per task on the 30-task dev slice (tasks 0-29).
- Held-out partition (tasks 30-49) is in eval/held_out/ and must NOT be touched until Act IV.
- Writes per-trial traces to eval/trace_log.jsonl.
- Writes aggregate stats to eval/score_log.json.
- Uses DeepSeek V3 via OpenRouter (openai/ prefix + OPENAI_API_BASE).

Usage:
    eval/tau2-bench/.venv/bin/python eval/harness.py [--trials 5] [--tasks 0-29]
"""
import argparse
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── tau2-bench path setup ──────────────────────────────────────────────────
TAU2_SRC = Path(__file__).parent / "tau2-bench" / "src"
TAU2_PYTHON = Path(__file__).parent / "tau2-bench" / ".venv" / "bin" / "python"
sys.path.insert(0, str(TAU2_SRC))

# ── OpenRouter via LiteLLM: use OPENAI_API_KEY + OPENAI_API_BASE ──────────
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")
if OPENROUTER_KEY and not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = OPENROUTER_KEY
if not os.getenv("OPENAI_API_BASE"):
    os.environ["OPENAI_API_BASE"] = "https://openrouter.ai/api/v1"

# ── Langfuse setup (optional) ──────────────────────────────────────────────
LANGFUSE_SECRET = os.getenv("LANGFUSE_SECRET_KEY")
LANGFUSE_PUBLIC = os.getenv("LANGFUSE_PUBLIC_KEY")
if LANGFUSE_SECRET and LANGFUSE_PUBLIC:
    os.environ["LANGFUSE_SECRET_KEY"] = LANGFUSE_SECRET
    os.environ["LANGFUSE_PUBLIC_KEY"] = LANGFUSE_PUBLIC

EVAL_DIR = Path(__file__).parent
TRACE_LOG = EVAL_DIR / "trace_log.jsonl"
SCORE_LOG = EVAL_DIR / "score_log.json"
HELD_OUT_DIR = EVAL_DIR / "held_out"

MODEL = os.getenv("TAU2_MODEL", "openai/deepseek/deepseek-chat-v3-0324")
DEV_SLICE = list(range(30))    # tasks 0-29
HELD_OUT_SLICE = list(range(30, 50))  # tasks 30-49 — do not touch

NUM_TRIALS = 5


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score confidence interval for a proportion."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def pass_at_k(trials: list[int], k: int = 1) -> float:
    """pass@k estimator. trials is a list of 0/1 per trial."""
    n = len(trials)
    if n == 0:
        return 0.0
    if k >= n:
        return float(any(trials))
    fail = sum(1 - t for t in trials)
    return 1.0 - math.comb(fail, k) / math.comb(n, k)


def run_harness(
    task_ids: list[int] | None = None,
    num_trials: int = NUM_TRIALS,
    model: str = MODEL,
    dry_run: bool = False,
) -> dict:
    """Run the τ²-Bench retail harness on the dev slice.

    Returns aggregate stats dict, also written to score_log.json.
    """
    try:
        from tau2.run import get_tasks, run_single_task
        from tau2.data_model.simulation import TextRunConfig
    except ImportError as e:
        print(f"ERROR: tau2 not importable — {e}")
        print("Run from the project root with: eval/tau2-bench/.venv/bin/python eval/harness.py")
        sys.exit(1)

    ids_to_run = task_ids if task_ids is not None else DEV_SLICE
    # Safety: never accidentally run the held-out partition
    safe_ids = [i for i in ids_to_run if i not in HELD_OUT_SLICE]
    if len(safe_ids) < len(ids_to_run):
        print(f"WARNING: Filtered {len(ids_to_run) - len(safe_ids)} held-out task IDs")

    all_tasks = get_tasks("retail")
    task_map = {int(t.id): t for t in all_tasks}
    tasks = [task_map[i] for i in safe_ids if i in task_map]
    print(f"Running {len(tasks)} tasks × {num_trials} trials — model={model}")

    config = TextRunConfig(
        domain="retail",
        agent="llm_agent",
        agent_llm=model,
        agent_llm_args={"temperature": 0.0},
        user="user_simulator",
        user_llm=model,
        user_llm_args={"temperature": 0.0},
        max_steps=100,
    )

    task_results: dict[int, list[float]] = {}
    all_latencies: list[float] = []
    total_cost = 0.0

    for task in tasks:
        task_id = int(task.id)
        task_results[task_id] = []

        for trial in range(num_trials):
            t0 = time.time()
            if dry_run:
                reward = 1.0 if (task_id + trial) % 3 != 0 else 0.0
                cost = 0.005
                latency_ms = 200
            else:
                try:
                    result = run_single_task(config, task, seed=trial * 100 + task_id)
                    reward = float(result.reward_info.reward if result.reward_info else 0.0)
                    # Cost from litellm if available
                    raw_cost = 0.0
                    try:
                        if hasattr(result, "usage") and result.usage:
                            pass  # tau2 doesn't expose cost directly
                    except Exception:
                        pass
                    cost = raw_cost
                    latency_ms = int((time.time() - t0) * 1000)
                except Exception as e:
                    print(f"  [FAIL] task={task_id} trial={trial}: {e}")
                    reward = 0.0
                    cost = 0.0
                    latency_ms = int((time.time() - t0) * 1000)

            task_results[task_id].append(reward)
            all_latencies.append(latency_ms)
            total_cost += cost

            trace_entry = {
                "task_id": task_id,
                "trial": trial,
                "reward": reward,
                "pass_fail": int(reward >= 1.0),
                "cost_usd": cost,
                "latency_ms": latency_ms,
                "model": model,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            with TRACE_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(trace_entry) + "\n")

            print(f"  task={task_id:3d} trial={trial} reward={reward:.2f} "
                  f"latency={latency_ms}ms")

    # ── Aggregate stats ───────────────────────────────────────────────────
    pass_at_1_per_task = {}
    for tid, trials in task_results.items():
        pass_at_1_per_task[tid] = pass_at_k(trials, k=1)

    all_pass_at_1 = list(pass_at_1_per_task.values())
    mean_pass_at_1 = sum(all_pass_at_1) / len(all_pass_at_1) if all_pass_at_1 else 0.0

    n_tasks = len(tasks)
    successes = sum(1 for v in all_pass_at_1 if v >= 0.5)
    ci_low, ci_high = wilson_ci(successes, n_tasks)

    all_latencies_sorted = sorted(all_latencies)
    n_lat = len(all_latencies_sorted)
    p50 = all_latencies_sorted[n_lat // 2] if n_lat else 0
    p95 = all_latencies_sorted[int(n_lat * 0.95)] if n_lat else 0

    avg_cost = total_cost / (n_tasks * num_trials) if n_tasks else 0

    stats = {
        "domain": "retail",
        "model": model,
        "num_tasks": n_tasks,
        "num_trials": num_trials,
        "pass_at_1_mean": round(mean_pass_at_1, 4),
        "pass_at_1_ci_low": round(ci_low, 4),
        "pass_at_1_ci_high": round(ci_high, 4),
        "pass_at_1_ci_95pct": f"[{ci_low:.3f}, {ci_high:.3f}]",
        "cost_per_run_usd": round(avg_cost, 6),
        "total_cost_usd": round(total_cost, 4),
        "p50_latency_ms": p50,
        "p95_latency_ms": p95,
        "per_task_pass_at_1": pass_at_1_per_task,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    SCORE_LOG.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(f"\n{'='*60}")
    print(f"pass@1 = {mean_pass_at_1:.3f}  95% CI = {stats['pass_at_1_ci_95pct']}")
    print(f"cost/run = ${avg_cost:.6f}  p50={p50}ms  p95={p95}ms")
    print(f"Results written → {SCORE_LOG}")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="τ²-Bench retail harness")
    parser.add_argument("--trials", type=int, default=NUM_TRIALS,
                        help=f"Trials per task (default {NUM_TRIALS})")
    parser.add_argument("--tasks", type=str, default="0-29",
                        help="Task range e.g. 0-29 (default dev slice)")
    parser.add_argument("--model", type=str, default=MODEL,
                        help="LiteLLM model string")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate results without calling the LLM (for CI testing)")
    args = parser.parse_args()

    # Parse task range
    if "-" in args.tasks:
        lo, hi = map(int, args.tasks.split("-"))
        task_ids = list(range(lo, hi + 1))
    else:
        task_ids = [int(x) for x in args.tasks.split(",")]

    run_harness(
        task_ids=task_ids,
        num_trials=args.trials,
        model=args.model,
        dry_run=args.dry_run,
    )
