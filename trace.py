import json
# trace.py — lightweight JSONL + console tracing for agent runs
import os
from datetime import datetime
from typing import Any, Dict, List, Union


def _short(text: str, limit: int = 200) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _truncate_value(value: Any, max_len: int) -> Any:
    """Shallow-ish truncate to keep logs readable."""
    if isinstance(value, str):
        return value if len(value) <= max_len else value[: max_len - 1] + "…"
    if isinstance(value, list):
        trimmed: List[Any] = []
        limit = 50
        for idx, item in enumerate(value):
            if idx >= limit:
                trimmed.append(f"... trimmed {len(value) - limit} items ...")
                break
            trimmed.append(_truncate_value(item, max_len))
        return trimmed
    if isinstance(value, dict):
        return {k: _truncate_value(v, max_len) for k, v in value.items()}
    return value


class TraceLogger:
    """
    Lightweight local logger: writes JSONL per task and prints concise console lines.
    """

    def __init__(
        self,
        task_id: str,
        debug: bool = True,
        log_dir: str = "logs/ooda_agent_erc",
        order: int | None = None,
        total: int | None = None,
        max_field_len: int | None = None,
    ) -> None:
        self.task_id = task_id
        self.debug = debug
        self.log_dir = log_dir
        self.order = order
        self.total = total
        self.max_field_len = max_field_len or int(os.getenv("MAX_FIELD_LEN", "4000"))
        os.makedirs(self.log_dir, exist_ok=True)
        self.path = os.path.join(self.log_dir, f"{task_id}.jsonl")
        # Keep in-memory buffer for JSON logging
        self.events: List[Dict[str, Any]] = []

    def log(self, event: str, data: Dict[str, Any], *, console: bool = False) -> None:
        record = {
            "ts": datetime.utcnow().isoformat(),
            "task": self.task_id,
            "event": event,
            "data": data,
        }
        safe_record = {
            "ts": record["ts"],
            "task": record["task"],
            "event": record["event"],
            "data": _truncate_value(data, self.max_field_len),
        }
        self.events.append(safe_record)
        mask_disk = os.getenv("TRACE_MASK_DISK", "0") != "0"
        with open(self.path, "a", encoding="utf-8") as f:
            json.dump(safe_record if mask_disk else record, f, ensure_ascii=False)
            f.write("\n")
        if console and self.debug:
            print(self._fmt_console(event, data))

    def _fmt_console(self, event: str, data: Dict[str, Any]) -> str:
        if event == "task_start":
            prefix = ""
            if self.order and self.total:
                prefix = f"[{self.order}/{self.total}] "
            return f"{prefix}[{self.task_id}] TASK {data.get('spec_id','')} :: {data.get('text','')}"
        if event == "step_start":
            return f"[{self.task_id}] STEP {data.get('step')} :: {data.get('note','')}"
        if event == "llm_request":
            return f"[{self.task_id}] LLM→ {data.get('hint','')}"
        if event == "llm_response":
            return f"[{self.task_id}] LLM← {_short(data.get('raw',''))}"
        if event == "tool_call":
            return f"[{self.task_id}] TOOL {data.get('name')} args={_short(json.dumps(data.get('args',{}), ensure_ascii=False),120)}"
        if event == "tool_result":
            status = "OK" if data.get("ok") else "ERR"
            return f"[{self.task_id}] TOOL {status} {_short(data.get('output',''))}"
        if event == "guard":
            return f"[{self.task_id}] GUARD {data.get('decision')}: {data.get('reason','')}"
        if event == "final":
            return f"[{self.task_id}] FINAL {data.get('outcome')}: {_short(data.get('message',''))}"
        return f"[{self.task_id}] {event}: {_short(str(data))}"

