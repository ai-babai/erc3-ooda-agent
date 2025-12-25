"""
Lightweight JSON logging for ooda_agent_erc.
Provides per-task files, session summary, and error reports.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from erc3 import TaskInfo


def get_session_hash(session_id: str) -> str:
    """Return short 4-letter hash to keep filenames compact."""
    if not session_id:
        return "xxxx"
    digest = hashlib.md5(session_id.encode("utf-8")).hexdigest()
    letters = []
    for i in range(4):
        num = int(digest[i * 2 : (i * 2) + 2], 16)
        letters.append(chr(ord("a") + (num % 26)))
    return "".join(letters)


def events_to_reasoning(events: List[Dict[str, Any]], *, max_field_len: int = 400) -> List[Dict[str, Any]]:
    """Convert TraceLogger events into compact reasoning log."""
    reasoning: List[Dict[str, Any]] = []
    for idx, ev in enumerate(events, start=1):
        name = ev.get("event", "unknown")
        ts = ev.get("ts")
        data = ev.get("data", {}) or {}
        summary = ""
        if name == "task_start":
            summary = f"Task start {data.get('task') or ''}"
        elif name in {"llm", "llm_request", "llm_response", "controller", "reflect"}:
            summary = f"{name}: {str(data.get('raw',''))[:max_field_len]}"
        elif name in {"step"}:
            summary = f"step {data.get('n')}"
        elif name in {"tool_call"}:
            summary = f"tool_call {data.get('name')}: {str(data.get('args'))[:max_field_len]}"
        elif name in {"tool_result"}:
            if data.get("ok") is False:
                summary = f"tool_error {data.get('name')}: {str(data.get('error'))[:max_field_len]}"
            else:
                summary = f"tool_result {data.get('name')}: {str(data.get('output'))[:max_field_len]}"
        else:
            summary = f"{name}: {str(data)[:max_field_len]}"
        reasoning.append({"step": idx, "timestamp": ts, "type": name, "summary": summary, "data": data})
    return reasoning


class LangJSONLogger:
    """Minimal JSON logger compatible with main.py expectations."""

    def __init__(self, base_dir: Optional[str] = None):
        base = base_dir or Path(__file__).resolve().parent / "task_logs"
        self.base_dir = Path(base)
        self.tasks_dir = self.base_dir / "tasks"
        self.sessions_dir = self.base_dir / "sessions"
        self.errors_dir = self.base_dir / "errors"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.errors_dir.mkdir(parents=True, exist_ok=True)
        self.session_id: Optional[str] = None
        self.session_hash: str = "xxxx"
        self.session_start: float = time.time()
        self.session_tasks: List[Dict[str, Any]] = []
        self.session_metadata: Dict[str, Any] = {}

    def start_session(self, session_id: str, metadata: Dict[str, Any], session_hash: Optional[str] = None):
        self.session_id = session_id
        self.session_hash = session_hash or get_session_hash(session_id)
        self.session_start = time.time()
        self.session_metadata = metadata or {}

    def log_task(
        self,
        task: TaskInfo,
        task_index: int,
        start_time: float,
        end_time: float,
        *,
        model_id: str,
        benchmark: str,
        architecture: str,
        success: bool,
        score: Optional[float],
        result_log: Optional[str],
        error: Optional[str],
        reasoning: Optional[List[Dict[str, Any]]],
        trace_events: Optional[List[Dict[str, Any]]],
    ) -> str:
        def classify_failure(log: Optional[str]) -> Optional[str]:
            if not log or success:
                return None
            lower = log.lower()
            if "unexpected" in lower and "event" in lower:
                return "unintended_side_effect"
            if "expected project link" in lower or "expected employee link" in lower:
                return "wrong_entity"
            if "expected outcome" in lower:
                return "wrong_outcome"
            if "expected event of type" in lower:
                return "action_not_called"
            if "not found" in lower and "expected" in lower:
                return "action_not_called"
            if "not found" in lower:
                return "not_found"
            if "expected" in lower and "outcome" in lower:
                return "wrong_outcome"
            if "wrong" in lower or "unexpected" in lower:
                return "wrong_entity"
            if "not called" in lower or "missing" in lower:
                return "action_not_called"
            return "other"

        duration = max(end_time - start_time, 0.0)
        ts_str = datetime.fromtimestamp(start_time).strftime("%m%d-%H-%M-%S")
        status = "pass" if success else "fail"
        filename = f"{self.session_hash}_task_{task_index:03d}_{ts_str}_{status}.json"
        path = self.tasks_dir / filename

        payload = {
            "task_id": task.task_id,
            "spec_id": task.spec_id,
            "task_text": task.task_text,
            "session_id": self.session_id,
            "session_hash": self.session_hash,
            "metadata": {
                "benchmark": benchmark,
                "architecture": architecture,
                "model_id": model_id,
                "start_time": datetime.fromtimestamp(start_time).isoformat(),
                "end_time": datetime.fromtimestamp(end_time).isoformat(),
                "duration_sec": round(duration, 2),
            },
            "success": success,
            "score": score,
            "result_log": result_log,
            "error": error,
            "reasoning": reasoning or [],
            "events": trace_events or [],
            "failure_type": classify_failure(result_log),
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        self.session_tasks.append(
            {
                "task_id": task.task_id,
                "spec_id": task.spec_id,
                "file": f"tasks/{filename}",
                "success": success,
                "score": score,
                "duration_sec": round(duration, 2),
            }
        )
        return str(path)

    def log_error(
        self,
        task: TaskInfo,
        task_index: int,
        *,
        result_log: Optional[str],
        error: Optional[str],
        reasoning: Optional[List[Dict[str, Any]]],
        trace_events: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        ts_base = self.session_start or time.time()
        ts_str = datetime.fromtimestamp(ts_base).strftime("%m%d-%H-%M-%S")
        filename = f"{self.session_hash}_error_task_{task_index:03d}_{ts_str}.txt"
        path = self.errors_dir / filename

        lines = []
        lines.append(f"Task: {task.task_id} ({task.spec_id})")
        lines.append(f"Session: {self.session_id} ({self.session_hash})")
        lines.append(f"Task text: {task.task_text}")
        if result_log:
            lines.append("\nResult log:")
            lines.append(result_log)
        if error:
            lines.append("\nError:")
            lines.append(str(error))
        if reasoning:
            lines.append("\nLast reasoning steps:")
            for entry in reasoning[-10:]:
                lines.append(f"- [{entry.get('timestamp')}] {entry.get('type')}: {entry.get('summary')}")
        if trace_events:
            lines.append("\nTrace events (latest first):")
            for ev in list(trace_events)[-20:]:
                ts = ev.get("ts")
                lines.append(f"- [{ts}] {ev.get('event')}: {ev.get('data')}")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return str(path)

    def finish_session(self, metadata: Dict[str, Any]) -> str:
        end_time = time.time()
        duration = end_time - (self.session_start or end_time)
        filename = f"{self.session_hash}_session_{self.session_id}_{datetime.fromtimestamp(self.session_start or end_time).strftime('%m%d-%H-%M-%S')}.json"
        path = self.sessions_dir / filename

        total = len(self.session_tasks)
        successful = sum(1 for t in self.session_tasks if t.get("success"))
        failed = total - successful
        success_rate = (successful / total * 100) if total else 0.0
        scores = [t.get("score") for t in self.session_tasks if t.get("score") is not None]
        avg_score = (sum(scores) / len(scores)) if scores else None

        payload = {
            "session_id": self.session_id,
            "session_hash": self.session_hash,
            "session_metadata": {
                **(metadata or {}),
                "start_time": datetime.fromtimestamp(self.session_start or end_time).isoformat(),
                "end_time": datetime.fromtimestamp(end_time).isoformat(),
                "duration_sec": round(duration, 2),
            },
            "tasks": self.session_tasks,
            "statistics": {
                "total_tasks": total,
                "successful": successful,
                "failed": failed,
                "success_rate": round(success_rate, 1),
                "avg_score": round(avg_score, 3) if avg_score is not None else None,
            },
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return str(path)

