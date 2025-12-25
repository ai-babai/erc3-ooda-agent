import argparse
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from erc3 import ERC3
from openai import OpenAI

# Load environment variables from .env file in the project root
PROJECT_ROOT = Path(__file__).resolve().parent

# Add project root to Python path if running as script
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import project modules (using relative imports when possible)
try:
    # Try absolute import first (when run as module)
    import ooda_agent_erc.load_env  # noqa: E402  # Loads .env file
    import ooda_agent_erc.config as cfg  # noqa: E402
    from ooda_agent_erc.config import ARCHITECTURE_NAME, MODEL_BASE_URL  # noqa: E402
    from ooda_agent_erc.trace import TraceLogger  # noqa: E402
    from ooda_agent_erc.json_logging import LangJSONLogger, events_to_reasoning, get_session_hash  # noqa: E402
    from ooda_agent_erc.agent import run_task  # noqa: E402
except ImportError:
    # Fallback to relative imports when run as script from project directory
    import load_env  # noqa: E402
    import config as cfg  # noqa: E402
    from config import ARCHITECTURE_NAME, MODEL_BASE_URL  # noqa: E402
    from trace import TraceLogger  # noqa: E402
    from json_logging import LangJSONLogger, events_to_reasoning, get_session_hash  # noqa: E402
    from agent import run_task  # noqa: E402


class RateLimiter:
    """Thread-safe rate limiter shared across workers."""

    def __init__(self, rps: float):
        self.min_interval = 1.0 / rps if rps > 0 else 0
        self.lock = threading.Lock()
        self.last_call = 0.0

    def acquire(self):
        with self.lock:
            now = time.time()
            wait = self.last_call + self.min_interval - now
            if wait > 0:
                time.sleep(wait)
            self.last_call = time.time()


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ERC3 agent")
    parser.add_argument("-b", "--benchmark", choices=["dev", "test", "prod"], default="dev")
    parser.add_argument("-t", "--task", dest="tasks", action="append")
    parser.add_argument("-m", "--model", dest="model")
    parser.add_argument("-w", "--workers", type=int, default=None, help="Parallel workers (default: 5)")
    parser.add_argument("--sequential", action="store_true", help="Force sequential execution")
    return parser.parse_args()


def select_task_subset(all_tasks, selectors):
    if not selectors:
        return all_tasks
    selected = []
    for sel in selectors:
        token = sel.strip()
        if token.isdigit():
            idx = int(token)
            if 1 <= idx <= len(all_tasks) and all_tasks[idx - 1] not in selected:
                selected.append(all_tasks[idx - 1])
        else:
            for task in all_tasks:
                if (task.task_id == token or task.spec_id == token) and task not in selected:
                    selected.append(task)
    return selected or all_tasks


def build_openai_client() -> OpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        env_path = PROJECT_ROOT / ".env"
        raise RuntimeError(
            f"OPENROUTER_API_KEY required. "
            f"Set it as environment variable or create .env file at {env_path} with:\n"
            f"OPENROUTER_API_KEY=your_api_key_here"
        )
    return OpenAI(api_key=api_key, base_url=MODEL_BASE_URL)


def execute_task(
    task, idx: int, total: int, client: OpenAI, core: ERC3, rate_limiter: RateLimiter, print_lock: threading.Lock
) -> dict:
    """Execute single task with rate limiting."""
    trace = TraceLogger(task.task_id, debug=False, order=idx, total=total)
    task_start = time.time()
    task_error = None

    try:
        rate_limiter.acquire()
        core.start_task(task)
        run_task(client, core, task, trace)
    except Exception as exc:
        task_error = str(exc)

    task_end = time.time()

    try:
        result = core.complete_task(task)
    except Exception as exc:
        result = None
        task_error = task_error or str(exc)

    score = getattr(result.eval, "score", None) if result and result.eval else None
    success = score == 1.0 if score is not None else False
    result_log = getattr(result.eval, "logs", None) if result and result.eval else None

    with print_lock:
        status = "✓" if success else "✗"
        duration = task_end - task_start
        print(f"[{idx:02d}/{total}] {status} {task.spec_id} ({duration:.1f}s) score={score}")
        if result_log and not success:
            print(f"         {result_log[:100]}...")

    return {
        "task": task,
        "idx": idx,
        "trace": trace,
        "task_start": task_start,
        "task_end": task_end,
        "task_error": task_error,
        "result": result,
        "score": score,
        "success": success,
        "result_log": result_log,
    }


def main():
    args = parse_cli_args()

    resolved_model, provider = cfg.resolve_model(getattr(args, "model", None))
    cfg.MODEL_ID = resolved_model
    cfg.MODEL_PROVIDER_BODY = provider

    max_workers = args.workers or cfg.MAX_WORKERS
    if args.sequential:
        max_workers = 1

    print(f"[config] Model: {cfg.MODEL_ID} | Workers: {max_workers}")

    client = build_openai_client()
    core = ERC3()
    run_hash = get_session_hash(str(time.time()))
    arch_with_hash = f"[{run_hash}] {ARCHITECTURE_NAME}"
    benchmark_name = f"erc3-{args.benchmark}"

    session = core.start_session(
        benchmark=benchmark_name,
        workspace="my",
        name=f"[{run_hash}] @skifmax OODA Agent ({cfg.MODEL_ID}) [{benchmark_name}]",
        architecture=arch_with_hash,
        flags=["compete_local"],
    )

    status = core.session_status(session.session_id)
    tasks_to_run = select_task_subset(status.tasks, args.tasks)
    total = len(tasks_to_run)
    print(f"Session {session.session_id} with {total} tasks")

    json_logger = LangJSONLogger()
    json_logger.start_session(
        session.session_id,
        {"benchmark": benchmark_name, "model_id": cfg.MODEL_ID, "workers": max_workers},
        run_hash,
    )

    rate_limiter = RateLimiter(cfg.RATE_LIMIT_RPS * max_workers)
    print_lock = threading.Lock()

    results = []
    session_start = time.time()

    if max_workers == 1:
        for idx, task in enumerate(tasks_to_run, 1):
            res = execute_task(task, idx, total, client, core, rate_limiter, print_lock)
            results.append(res)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(execute_task, task, idx, total, client, core, rate_limiter, print_lock): idx
                for idx, task in enumerate(tasks_to_run, 1)
            }
            for future in as_completed(futures):
                try:
                    res = future.result()
                    results.append(res)
                except Exception as exc:
                    idx = futures[future]
                    print(f"[{idx:02d}/{total}] ✗ EXCEPTION: {exc}")

    session_end = time.time()
    results.sort(key=lambda r: r["idx"])

    for res in results:
        try:
            reasoning = events_to_reasoning(res["trace"].events)
            json_logger.log_task(
                task=res["task"],
                task_index=res["idx"],
                start_time=res["task_start"],
                end_time=res["task_end"],
                model_id=cfg.MODEL_ID,
                benchmark=benchmark_name,
                architecture=arch_with_hash,
                success=res["success"],
                score=res["score"],
                result_log=res["result_log"],
                error=res["task_error"],
                reasoning=reasoning,
                trace_events=res["trace"].events,
            )
            if not res["success"]:
                json_logger.log_error(
                    task=res["task"],
                    task_index=res["idx"],
                    result_log=res["result_log"],
                    error=res["task_error"],
                    reasoning=reasoning,
                )
        except Exception:
            pass

    core.submit_session(session.session_id)
    json_logger.finish_session({"benchmark": benchmark_name, "model_id": cfg.MODEL_ID})

    successful = sum(1 for r in results if r["success"])
    duration = session_end - session_start
    print("=" * 50)
    print(f"Done: {successful}/{total} ({100*successful/total:.0f}%) in {duration:.1f}s")
    print(f"Avg: {duration/total:.1f}s/task | Throughput: {total/duration:.1f} tasks/min")


if __name__ == "__main__":
    main()

