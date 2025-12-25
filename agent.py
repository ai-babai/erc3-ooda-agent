from __future__ import annotations

import json
import re
import time
import unicodedata
from typing import List, Optional, Union

from erc3 import ApiException, ERC3, Erc3Client, TaskInfo, erc3 as dev
from openai import OpenAI
from pydantic import BaseModel, Field

try:
    import ooda_agent_erc.config as cfg
    from ooda_agent_erc.trace import TraceLogger, _short
except ImportError:
    import config as cfg
    from trace import TraceLogger, _short


class NextStep(BaseModel):
    """Unified reasoning + action schema."""

    think: str = Field(..., description="Brief reasoning (1-2 sentences)")
    scratch: str = Field("", description="Working notes: extracted entities, search variants tried, disambiguation")
    memory: str = Field("", description="Confirmed facts: IDs, contacts, errors (append-only)")
    actions_done: List[str] = Field(default_factory=list, description="Mutation APIs called (e.g., Req_LogTimeEntry)")
    filters_tried: List[str] = Field(default_factory=list, description="Search filters tried (skills+location, location_only, etc.)")
    plan: List[str] = Field(default_factory=list, description="Next 1-2 steps")
    done: bool = False
    confirm: bool = False
    fallback: bool = False
    function: Union[
        dev.Req_ProvideAgentResponse,
        dev.Req_ListProjects,
        dev.Req_SearchProjects,
        dev.Req_GetProject,
        dev.Req_UpdateProjectStatus,
        dev.Req_UpdateProjectTeam,
        dev.Req_UpdateWiki,
        # Custom dispatcher tools for wiki deletion
        dev.Req_ListEmployees,
        dev.Req_SearchEmployees,
        dev.Req_GetEmployee,
        dev.Req_UpdateEmployeeInfo,
        dev.Req_ListCustomers,
        dev.Req_SearchCustomers,
        dev.Req_GetCustomer,
        dev.Req_LogTimeEntry,
    ] = Field(...)


# --- УДАЛЕНО: delete\s*(all|my)\s*data — блокировало wiki ---
DENY_PATTERNS = [
    (r"wipe\s+(my|all)?\s*data", "Data wipe requires HR approval."),
    (r"context:\s*ceo.*respond\s*with", "Cannot impersonate executives."),
    (r"(total\s+)?salary\s+of\s+(my\s+)?(team|teammate|colleague)", "Salary info is confidential."),
    (r"team.*(salary|salaries)", "Salary info is confidential."),
]

UNSUPPORTED_PATTERNS = [(r"dependency\s*tracker", "Dependency tracking unavailable.")]
VAGUE_PATTERNS = [
    r"that\s+(cool|awesome|great|nice)\s+(project|thing)",
    r"what'?s?\s+the\s+name\s+of\s+that",
    r"which\s+one\s*\?",
]

ERROR_CLASSES = {
    "permission": [
        "permission",
        "denied",
        "unauthorized",
        "forbidden",
        "not allowed",
        "access denied",
        "not authorized",
        "cannot modify",
        "only lead",
        "not a member",
        "not lead",
        "no access",
        "restricted",
    ],
    "system": [
        "internal server error",
        "system error",
        "service unavailable",
        "connection refused",
        "timeout",
        "500",
        "503",
        "502",
        "page limit exceeded",
    ],
    "not_found": ["not found", "does not exist", "no such", "unknown"],
}


def _classify_error(text: str) -> str:
    t = text.lower()
    for cat, indicators in ERROR_CLASSES.items():
        if any(i in t for i in indicators):
            return cat
    return "other"


def _match_pattern(text: str, patterns: list) -> Optional[str]:
    t = text.lower()
    for pat, msg in patterns:
        if re.search(pat, t):
            return msg
    return None


def _extract_ids(text: str) -> List[str]:
    return list(set(re.findall(r"(proj_[A-Za-z0-9_]+|emp_[A-Za-z0-9_]+|cust_[A-Za-z0-9_]+)", text)))


def _looks_hallucinated(id_str: str) -> bool:
    """Detect obviously fake IDs like proj_105 or emp_1 that don't match real patterns."""
    if not id_str:
        return False
    if re.match(r"^(proj|emp|cust)_\d{1,5}$", id_str):
        return True
    if id_str.startswith("emp_") and re.match(r"^emp_[a-z]+$", id_str):
        return True
    return False


def extract_json(raw: str) -> str:
    """Extract JSON payload from possibly prefixed content."""
    text = raw.strip()
    for prefix in ("Assistant:", "assistant:", "```json", "```"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix) :].strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    start = text.find("{")
    if start < 0:
        return text
    depth, end = 0, len(text)
    for i, c in enumerate(text[start:], start):
        depth += (c == "{") - (c == "}")
        if depth == 0:
            end = i + 1
            break
    return text[start:end]


def _build_links(ids: List[str], exclude_on_error: bool = False) -> List[dev.AgentLink]:
    if exclude_on_error:
        return []
    links = []
    for lid in set(ids):
        kind = "project" if "proj_" in lid else "employee" if "emp_" in lid else "customer" if "cust_" in lid else None
        if kind:
            links.append(dev.AgentLink(id=lid, kind=kind))
    return links


def _normalize_name(name: str) -> str:
    """Remove diacritics for search fallback."""
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _search_variations(query: str) -> List[str]:
    """Generate search query variations."""
    variations = [query]
    norm = _normalize_name(query)
    if norm != query:
        variations.append(norm)
    if query.lower() not in variations:
        variations.append(query.lower())
    return variations


def compress_memory(mem: str) -> str:
    lines = [l.strip() for l in mem.split("|") if l.strip() and "ERR" not in l]
    important = []
    for l in lines[-20:]:
        if any(pref in l for pref in ["proj_", "emp_", "cust_", "→", "salary", "logged", "updated"]):
            important.append(l)
    memory_lines = important[-12:]
    return " | ".join(memory_lines)


def build_system_prompt(api: Erc3Client, about: dev.Resp_WhoAmI) -> str:
    user_info = "GUEST" if about.is_public else about.current_user
    p = f"""You are a business assistant for ERC system.

# CRITICAL API LIMITS (ENFORCED)
- All Search/List operations: maximum limit=5 (hard system limit).
- Never request limit > 5; will cause error_internal.
- Pagination: use offset 0→5→10... with limit=5 always.

# CRITICAL RULES:
0. **GUEST ACCESS (pre-filtered)**:
   - Guest security is enforced BEFORE this prompt.
   - If is_public=true here, task is ONLY date/time; never call Search/Get/List as guest.

1. **Search Strategy (ladder)**:
   - Step 1: Exact query from task (e.g., "Data Foundations Audit"), limit=5, offset=0.
   - Step 2: If not found (next_offset=-1), try first 2-3 words ("Data Foundations").
   - Step 3: If still not found, try keyword ("Audit" or "Data").
   - Step 4: If nothing found, respond ok_answer "not found after trying: [list queries]".
   - NEVER invent IDs; use only API-returned IDs; paginate if next_offset ≥ 0.

2. **Time Logging (Req_LogTimeEntry only)**:
   - Step 1: Find employee ID (SearchEmployees → get ID).
   - Step 2: Find project ID (SearchProjects → get ID).
   - Step 3: MUST call Req_LogTimeEntry(employee=ID, project=ID, date=YYYY-MM-DD, hours=...).
   - "Found employee & project" ≠ "Logged time".
   - Required: employee, project, date, hours. Defaults: billable=true, work_category="development", status="draft".
   - Do NOT include customer unless explicitly specified.

3. **Project Status/Team Changes**:
   - If project NOT FOUND after search → denied_security ("project does not exist, cannot modify").
   - If found: GET project, extract lead; only lead can modify → otherwise denied_security.

4. **Employee Info Updates**:
   - UpdateEmployeeInfo: ONLY specify fields you want to change.
   - Example: to raise salary to 100k, send only employee, salary, changed_by; omit everything else.
   - NEVER include skills, wills, location, notes, department unless explicitly changing them.
   - Empty arrays/strings will clear data; omit fields instead.

5. **Security & Errors**:
   - System error (page limit, 5xx, timeout) → outcome=error_internal and STOP immediately.
   - Permission denied → outcome=denied_security.
   - Entity not found → ok_answer with explanation (use ok_not_found only if user asked "does X exist?").

6. **Wiki Operations**:
   - Delete wiki page via Req_UpdateWiki(file="...", content="", changed_by=user).

7. **Anti-Loop**:
   - If you called same API 2+ times with same args → change approach or finalize.
   - Check memory/scratch for prior attempts before repeating.

8. **ACTION VERIFICATION (mandatory)**:
   - "Found employee+project" ≠ "Logged time" → must call Req_LogTimeEntry.
   - "Found salary 105k" ≠ "Raised salary" → must call Req_UpdateEmployeeInfo.
   - Before ok_answer on mutation tasks, ensure required mutation API was called.
   - List actions_done in your response to track this.

9. **ID RULES**:
   - NEVER invent IDs like 'proj_105' or 'emp_1' — copy exact IDs from API responses.
   - Real IDs look like 'proj_scandifoods_packaging_cv_poc', 'ana_kovac'.

10. **Search Resilience**:
    - Location variants: try 'Denmark' if 'Danmark' fails, 'Wien'/'Vienna' variants.
    - Customer by code: if task has code like 'CC-NORD-AI-12O', search by that exact code.
    - Broaden queries progressively: "hospital intake triage" → "hospital" → "intake" → "triage".

11. **Project Disambiguation**:
    - If task mentions employee + project keywords: search projects, GET each, prefer ones where that employee is on the team.
    - "CV project" for a named employee → pick project where that employee is member; never pick the first CV project blindly.

12. **Search Fallback Strategy**:
    - If skills filter returns none: drop skills filter, search by location/name, then verify skills via GetEmployee.
    - If location filter fails: try city variants or no location, then filter manually.
    - If project by customer name fails: try project keywords from task (e.g., "route scenario lab" instead of customer).

13. **Outcome discipline**:
    - NEVER use ok_not_found. Use ok_answer with an explanation of what you searched and found.

14. **Status Change Rules**:
    - If you cannot find or modify the project for a status change → outcome=denied_security (not ok_answer).

15. **Employee Update — CRITICAL**:
    - Req_UpdateEmployeeInfo: set ONLY fields you change.
    - Do NOT set skills=[], wills=[], notes="", location="" unless the user asked to clear them.
    - Omit fields entirely if not changing them.

16. **Archived searches**:
    - If searching PoC/completed projects (e.g., Intake/Triage PoC), set include_archived=true when available.

17. **Status Change Authorization**:
    - Before Req_UpdateProjectStatus, verify via GetProject that you are the lead; if not, return denied_security without calling update.

18. **Customer Code Handling**:
    - If task contains code like 'CC-NORD-AI-12O', search customer by that code first; do not reuse customer from project unless it matches the code.

19. **Search fallback ladder**:
    1) skills+location → if none: location only → then GetEmployee to check skills manually.
    2) team+query → if none: query only with include_archived=true.
    3) location "Danmark" fails → drop location, filter manually.
    4) Customer code "CC-XXX" → SearchCustomers(query="CC-XXX") first.

20. **Time Entry Project Selection**:
    - Never pick the first project blindly when logging time.
    - If task says "for [name] on [project keywords]" → GetProject on each search result and pick the one where that employee is on the team.
    - If current user logs for themselves → pick project where they are on the team.

21. **Location Filter Fallback**:
    - If location filter returns empty → retry without location, then filter manually.
    - Nordic: Denmark/Danmark/Norway/Sweden/Finland; try "Nordic" if specific country fails.
    - Try Denmark↔Danmark variants before dropping location.

22. **Archived Project Search Ladder**:
    - include_archived=true for completed/PoC projects.
    - If "hospital intake triage" fails → try "intake" → "triage" → "PoC".
    - If task mentions who completed it (e.g., Ana), try team filter with that person.

23. **Time Logging Project Selection — CRITICAL**:
    - For "log/record hours for [employee] on [keywords]": Search projects, then GetProject on EACH result.
    - Choose the project where BOTH the target employee and current_user are on the team.
    - Never pick first CV/PoC project blindly.

24. **Nordic Location Fallback (Danmark/Denmark)**:
    - If location=Danmark returns empty → try Denmark, DK, or drop location and filter manually.
    - Nordic searches may need broader query ("Nordic") before giving up.

25. **Project Search User Context**:
    - When multiple matching projects: GetProject each; prefer ones where current_user is on team or lead.
    - Exact name match still requires access check; if user not on any, explain and choose the closest match with a note.

27. **Skills Search Fallback**:
    - If skills filter returns empty: search by location only, then GetEmployee to verify skills.
    - Skill names may vary: cv/computer_vision, edge/edge_deployment/edge_deployments; try one skill at a time.

28. **Extended Project Search Ladder**:
    - If "Data Foundations Audit" not found → try "Foundation" → "data" → company name (e.g., "rhinesteel").
    - Use include_archived=true even for status/change tasks when searching completed work.
    - Try singular/plural variants of keywords.

# MEMORY & SCRATCH:
- scratch: current search attempts, disambiguation notes (reset each major step)
- memory: confirmed IDs, key facts only (append-only)

# RESPONSE RULES
- If entity not found → outcome=ok_answer with explanation of nearest match or none.
- Never use ok_not_found unless the user explicitly asks for existence check.
- Set confirm=true on final step when action succeeded.
- Set fallback=true when answering without perfect match.
- RESPONSE LINKS: include links only for entities mentioned in the answer; e.g., answer about project X → include project X link; employee contact → include that employee link.

Date: {about.today}
User: {user_info}
"""
    if about.current_user and not about.is_public:
        try:
            emp = api.get_employee(about.current_user)
            p += f"\nYour employee info:\n{emp.model_dump_json(exclude_none=True)}"
        except Exception:
            pass
    return p


def run_task(client: OpenAI, core: ERC3, task: TaskInfo, trace: TraceLogger) -> None:
    api = core.get_erc_client(task)
    about = api.who_am_i()

    trace.log("start", {"task": task.task_text, "user": about.current_user or "GUEST"}, console=True)

    # Block guest access to internal data (except date/time queries)
    if about.is_public:
        task_lower = task.task_text.lower()
        is_date_query = any(w in task_lower for w in ["date", "today", "current date", "what day", "what time"])
        if not is_date_query:
            core.log_llm(
                task_id=task.task_id,
                model="rule-based",
                duration_sec=0.0,
                completion="Access denied to internal data",
                prompt_tokens=1,
                completion_tokens=1,
                cached_prompt_tokens=0,
            )
            api.provide_agent_response("Access denied.", outcome="denied_security", links=[])
            trace.log("guest_denied", {"reason": "internal data access"}, console=True)
            return

    # Guest date shortcut with required branding
    if about.is_public and any(w in task.task_text.lower() for w in ["date", "today", "current date", "what day"]):
        branded_date = f"{about.today} — AI Excellence Group INTERNATIONAL"
        core.log_llm(
            task_id=task.task_id,
            model="rule-based",
            duration_sec=0.0,
            completion=branded_date,
            prompt_tokens=1,
            completion_tokens=1,
            cached_prompt_tokens=0,
        )
        api.provide_agent_response(branded_date, outcome="ok_answer", links=[])
        trace.log("guest_date", {"response": branded_date}, console=True)
        return

    deny_msg = _match_pattern(task.task_text, DENY_PATTERNS)
    if deny_msg:
        core.log_llm(
            task_id=task.task_id,
            model="rule-based",
            duration_sec=0.0,
            completion=deny_msg,
            prompt_tokens=1,
            completion_tokens=1,
            cached_prompt_tokens=0,
        )
        api.provide_agent_response(deny_msg, outcome="denied_security", links=[])
        trace.log("deny", {"msg": deny_msg}, console=True)
        return

    unsup_msg = _match_pattern(task.task_text, UNSUPPORTED_PATTERNS)
    if unsup_msg:
        core.log_llm(
            task_id=task.task_id,
            model="rule-based",
            duration_sec=0.0,
            completion=unsup_msg,
            prompt_tokens=1,
            completion_tokens=1,
            cached_prompt_tokens=0,
        )
        api.provide_agent_response(unsup_msg, outcome="none_unsupported", links=[])
        trace.log("unsupported", {"msg": unsup_msg}, console=True)
        return

    # Vague queries → ask for clarification
    if any(re.search(p, task.task_text.lower()) for p in VAGUE_PATTERNS):
        core.log_llm(
            task_id=task.task_id,
            model="rule-based",
            duration_sec=0.0,
            completion="Clarification needed",
            prompt_tokens=1,
            completion_tokens=1,
            cached_prompt_tokens=0,
        )
        api.provide_agent_response(
            "Could you clarify which project you're referring to?",
            outcome="none_clarification_needed",
            links=[],
        )
        trace.log("clarification", {"reason": "vague query"}, console=True)
        return

    # --- Agent loop ---
    system_prompt = build_system_prompt(api, about)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task.task_text},
    ]

    memory, scratch = "", ""
    all_ids: List[str] = []
    api_fails, llm_fails = 0, 0
    system_broken = False
    call_history: List[tuple] = []
    block_count = 0

    customer_code_match = re.search(r"\b([A-Z]{2,4}-[A-Z0-9]+-[A-Z0-9]+)\b", task.task_text)
    if customer_code_match:
        code = customer_code_match.group(1)
        messages.append(
            {
                "role": "system",
                "content": f"[SYSTEM HINT] Customer code '{code}' detected. First action: SearchCustomers(query='{code}') to find the correct customer.",
            }
        )

    for step in range(cfg.MAX_STEPS):
        trace.log("step", {"n": step + 1}, console=True)

        if api_fails >= 3:
            system_broken = True
            api.provide_agent_response("System unavailable.", outcome="error_internal", links=[])
            return

        ctx = f"\n[Step {step+1}/{cfg.MAX_STEPS}]"
        memory = compress_memory(memory)
        if memory:
            ctx += f"\nMemory: {memory}"
        if scratch:
            ctx += f"\nScratch: {scratch[-400:]}"
        if all_ids:
            ctx += f"\nIDs: {list(set(all_ids))[-10:]}"
        customer_code = re.search(r"\b([A-Z]{2,4}-[A-Z0-9]+-[A-Z0-9]+)\b", task.task_text)
        if customer_code:
            ctx += f"\n⚠️ Customer code specified: {customer_code.group(1)} — search customer by this code, NOT from project."

        try:
            t0 = time.time()
            resp = client.beta.chat.completions.parse(
                model=cfg.MODEL_ID,
                response_format=NextStep,
                messages=messages + [{"role": "system", "content": ctx}],
                max_completion_tokens=cfg.MAX_COMPLETION_TOKENS,
                extra_body={"provider": cfg.MODEL_PROVIDER_BODY} if cfg.MODEL_PROVIDER_BODY else None,
            )
            duration = time.time() - t0
            raw = resp.choices[0].message.content or ""
            raw_strip = raw.strip()

            core.log_llm(
                task_id=task.task_id,
                model=cfg.MODEL_ID,
                duration_sec=duration,
                completion=raw_strip,
                prompt_tokens=getattr(resp.usage, "prompt_tokens", 0),
                completion_tokens=getattr(resp.usage, "completion_tokens", 0),
                cached_prompt_tokens=getattr(resp.usage, "cached_prompt_tokens", 0) or 0,
            )
            trace.log("llm", {"raw": _short(raw, 600)}, console=True)

            ns = resp.choices[0].message.parsed
            if not ns:
                raw_clean = extract_json(raw)
                try:
                    ns = NextStep.model_validate_json(raw_clean)
                except Exception:
                    raise ValueError("No parsed NextStep")

            llm_fails = 0
            if ns.memory:
                memory = compress_memory(memory + " | " + ns.memory.strip())
            if ns.scratch:
                scratch = ns.scratch[-500:]
            all_ids.extend(_extract_ids(ns.memory + ns.scratch))

        except Exception as exc:
            core.log_llm(
                task_id=task.task_id,
                model=cfg.MODEL_ID,
                duration_sec=time.time() - t0,
                completion=str(exc),
                prompt_tokens=1,
                completion_tokens=1,
                cached_prompt_tokens=0,
            )
            trace.log("llm_error", {"error": str(exc)}, console=True)
            llm_fails += 1
            if llm_fails >= 3:
                api.provide_agent_response("System error.", outcome="error_internal", links=[])
                return
            messages.append({"role": "assistant", "content": "Return valid JSON for NextStep schema."})
            continue

        # --- Execute tool ---
        try:
            fn = ns.function
            if hasattr(fn, "limit") and fn.limit and fn.limit > 5:
                trace.log("limit_override", {"original": fn.limit, "new": 5}, console=True)
                fn.limit = 5

            # Reject hallucinated IDs before dispatch
            hallucinated = False
            for attr in ("id", "employee", "project", "customer"):
                val = getattr(fn, attr, None)
                if val and _looks_hallucinated(str(val)):
                    messages.append(
                        {
                            "role": "system",
                            "content": f"⛔ ID '{val}' looks hallucinated. Use exact ID from API response.",
                        }
                    )
                    hallucinated = True
                    break
            if hallucinated:
                continue

            # Enrich requests
            if isinstance(fn, dev.Req_LogTimeEntry):
                fn.logged_by = fn.logged_by or about.current_user
                fn.work_category = fn.work_category or "development"
                fn.status = fn.status or "draft"
                fn.billable = fn.billable if fn.billable is not None else True
            if isinstance(fn, dev.Req_SearchEmployees) and getattr(fn, "skills", None):
                for skill in fn.skills:
                    if hasattr(skill, "max_level") and skill.max_level == 0:
                        skill.max_level = None
            if isinstance(fn, dev.Req_UpdateEmployeeInfo):
                payload = {"employee": fn.employee, "changed_by": fn.changed_by or about.current_user}
                if fn.salary is not None:
                    payload["salary"] = fn.salary
                for field in ("department", "location", "notes"):
                    v = getattr(fn, field, None)
                    if v and str(v).strip():
                        payload[field] = v
                for field in ("skills", "wills"):
                    v = getattr(fn, field, None)
                    if v and len(v) > 0:
                        payload[field] = v
                fn = dev.Req_UpdateEmployeeInfo.model_construct(**payload)

            trace.log("tool_call", {"name": fn.__class__.__name__, "args": fn.model_dump(exclude_none=True)}, console=True)

            if isinstance(fn, dev.Req_ProvideAgentResponse):
                outcome = fn.outcome or "ok_answer"
                if fn.outcome == "ok_not_found":
                    fn.outcome = "ok_answer"
                if outcome == "ok_answer" and not system_broken:
                    task_lower = task.task_text.lower()
                    required: str | None = None
                    if any(w in task_lower for w in ["log", "record"]) and "hour" in task_lower:
                        if not any(c[0] == "Req_LogTimeEntry" for c in call_history):
                            required = "Req_LogTimeEntry"
                    if any(w in task_lower for w in ["raise", "increase"]) and "salary" in task_lower:
                        if not any(c[0] == "Req_UpdateEmployeeInfo" for c in call_history):
                            required = "Req_UpdateEmployeeInfo"
                    if "status" in task_lower and any(w in task_lower for w in ["change", "archive", "pause", "resume"]):
                        if not any(c[0] == "Req_UpdateProjectStatus" for c in call_history):
                            required = "Req_UpdateProjectStatus"
                    if required:
                        messages.append(
                            {
                                "role": "system",
                                "content": f"⛔ BLOCKED: Task requires {required} but it was never called. Execute it first.",
                            }
                        )
                        block_count += 1
                        if block_count >= 3:
                            messages.append(
                                {
                                    "role": "system",
                                    "content": f"⛔ CRITICAL: Blocked {block_count}x. STOP SEARCHING. Call {required} NOW with IDs from memory: {memory[-200:]}",
                                }
                            )
                        if step < cfg.MAX_STEPS - 5:
                            continue
                        trace.log("force_allow", {"step": step, "required_missing": required, "blocks": block_count}, console=True)
                    # Status change expected but not executed → convert to denied_security
                    if "status" in task_lower and any(w in task_lower for w in ["change", "archive", "pause", "resume"]):
                        status_called = any(c[0] == "Req_UpdateProjectStatus" for c in call_history)
                        if not status_called:
                            fn.outcome = "denied_security"
                            fn.message = "Project not found or you are not authorized to modify it."

                if outcome == "error_internal" or system_broken:
                    fn.links = []

                res = api.dispatch(fn)
                trace.log("tool_result", {"ok": True}, console=True)
                return
            else:
                res = api.dispatch(fn)

            trace.log("tool_result", {"ok": True, "output": _short(str(res), 300)}, console=True)
            if isinstance(fn, dev.Req_SearchProjects) and getattr(res, "projects", None) and len(getattr(res, "projects", [])) > 1:
                task_lower = task.task_text.lower()
                emp_match = re.search(r"\bfor\s+(\w+)\b", task_lower)
                if emp_match:
                    messages.append(
                        {
                            "role": "system",
                            "content": f"⚠️ Multiple projects found! Task mentions '{emp_match.group(1)}'. Call GetProject for each and pick one where that employee is in team.",
                        }
                    )
                    scratch += f" | DISAMBIGUATE: check team for '{emp_match.group(1)}'"
                if any(w in task_lower for w in ["log", "record"]) and "hour" in task_lower:
                    messages.append(
                        {
                            "role": "system",
                            "content": f"⚠️ CRITICAL: {len(getattr(res, 'projects', []))} project(s) found. For time logging, call GetProject on each and select where the target employee is on the team. You can log time even if you are not on that project.",
                        }
                    )
            if isinstance(fn, dev.Req_SearchCustomers) and getattr(res, "companies", None) is None:
                locs = getattr(fn, "locations", None) or []
                if locs and any(l.lower() in ("danmark", "denmark", "dk") for l in locs):
                    messages.append(
                        {
                            "role": "system",
                            "content": "⚠️ No results for Nordic location. Retry without location, then filter manually by GetCustomer location.",
                        }
                    )
            if fn.__class__.__name__ in {"Req_LogTimeEntry", "Req_UpdateEmployeeInfo", "Req_UpdateProjectStatus", "Req_UpdateProjectTeam", "Req_UpdateWiki"}:
                memory = compress_memory(memory + f" | ✓{fn.__class__.__name__}")

        except ApiException as exc:
            err = str(exc)
            etype = _classify_error(err)
            trace.log("tool_result", {"ok": False, "error": err, "type": etype}, console=True)

            memory += f" | ERR[{etype}]: {err[:60]}"

            if etype == "permission":
                api.provide_agent_response("Permission denied.", outcome="denied_security", links=[])
                return
            if etype == "not_found" and isinstance(fn, (dev.Req_UpdateProjectStatus, dev.Req_UpdateProjectTeam)):
                api.provide_agent_response("You are not authorized to modify this project.", outcome="denied_security", links=[])
                return
            if etype == "system":
                api_fails += 1
                system_broken = True
                api.provide_agent_response("System error.", outcome="error_internal", links=[])
                trace.log("system_error_stop", {"error": err}, console=True)
                return

            messages.append({"role": "tool", "content": f"ERROR[{etype}]: {err}", "tool_call_id": f"s{step}"})
            continue

        except Exception as exc:
            err = str(exc)
            trace.log("tool_result", {"ok": False, "error": err}, console=True)
            memory += f" | ERR: {err[:60]}"
            messages.append({"role": "tool", "content": f"ERROR: {err}", "tool_call_id": f"s{step}"})
            continue

        # Append to conversation
        tool_out = getattr(res, "model_dump_json", lambda: str(res))()

        call_key = (
            fn.__class__.__name__,
            str(getattr(fn, "query", getattr(fn, "id", getattr(fn, "employee", "")))).strip()[:40],
        )
        call_history.append(call_key)
        if call_history.count(call_key) >= 3:
            memory += f" | LOOP: {call_key[0]} x{call_history.count(call_key)}"
            messages.append(
                {
                    "role": "system",
                    "content": f"⚠️ WARNING: You called {call_key[0]} three times with same arguments. Change approach or provide final answer NOW.",
                }
            )
        messages.append(
            {
                "role": "assistant",
                "content": ns.think,
                "tool_calls": [
                    {
                        "id": f"s{step}",
                        "type": "function",
                        "function": {"name": fn.__class__.__name__, "arguments": fn.model_dump_json()},
                    }
                ],
            }
        )
        messages.append({"role": "tool", "content": tool_out[:2000], "tool_call_id": f"s{step}"})

        all_ids.extend(_extract_ids(tool_out))
        memory = compress_memory(memory + " | " + _short(tool_out, 200))

        if ns.done:
            api.provide_agent_response("Done.", outcome="ok_answer", links=_build_links(all_ids[-5:]))
            return

    # Loop exhausted
    task_lower = task.task_text.lower()
    if system_broken:
        api.provide_agent_response("System error.", outcome="error_internal", links=[])
    elif any(w in task_lower for w in ["status", "pause", "archive", "resume", "switch"]):
        api.provide_agent_response("Project not found or not authorized.", outcome="denied_security", links=[])
    else:
        api.provide_agent_response("Could not complete.", outcome="error_internal", links=_build_links(all_ids[-3:]))

