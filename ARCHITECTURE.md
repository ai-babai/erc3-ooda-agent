# Architecture — Single-Agent OODA Loop & Hybrid Guardrails

This document describes the **actual** architecture implemented in this repository (as-is), and highlights where common “high-level descriptions” can be slightly misleading.

---

## 🇷🇺 Русская версия

### Обзор

Агент реализован как **одноагентный цикл OODA (Observe → Orient → Decide → Act)** поверх ERC3 SDK (`erc3`) и OpenAI-compatible клиента (`openai`) через OpenRouter.

Ядро — Pydantic-схема Structured Outputs `NextStep`, которая заставляет модель возвращать **структурированное решение на каждом шаге**:

1. **think** — краткое рассуждение (1–2 предложения)
2. **scratch** — рабочие заметки/гипотезы (в коде хранится только хвост до 500 символов)
3. **memory** — подтверждённые факты/ID (в коде это строка, которая постоянно сжимается)
4. **function** — конкретный вызов инструмента (типизированные запросы ERC3)

### OODA Loop (как это работает в коде)

1. **Observe**
   - Получение контекста сессии и пользователя через `who_am_i()`
   - Получение текста задачи из `TaskInfo`

2. **Orient**
   - Формирование динамического system prompt (`build_system_prompt`) с учётом `Guest vs Authenticated`
   - В промпт зашиты “Search Ladders” и правила работы с лимитами API (limit=5)
   - Подготовка контекстного блока шага: текущая память, scratch, последние ID

3. **Decide**
   - Вызов LLM через `client.beta.chat.completions.parse(..., response_format=NextStep, ...)`
   - На выходе получаем строго типизированный объект `NextStep`

4. **Act**
   - Выполнение `ns.function` через `api.dispatch(fn)`
   - Обработка ошибок (`ApiException`) и классификация по типам (permission/system/not_found/other)
   - Обновление `memory`, `scratch`, сбор ID, контроль повторов (anti-loop)

Цикл повторяется до:
- явного `Req_ProvideAgentResponse` (финальный ответ),
- или `done=true`,
- или исчерпания `MAX_STEPS`.

---

## Guardrails (Hybrid)

### 1) Pre-generation (до LLM)

- **Guest guard**: если `about.is_public=true`, агент запрещает вызовы Search/Get/List (кроме date/time сценариев)
- **Regex deny-list**: блокировка “salary”, “wipe data”, попыток импёрсонации и т.п.
- **Unsupported patterns / Vague queries**: отдельные быстрые отказы и запрос на уточнение

### 2) Anti-hallucination (перед dispatch)

Перед отправкой запроса в API выполняется эвристическая проверка ID:
- блокируются *очевидно фейковые* форматы (`proj_105`, `emp_1` и некоторые `emp_xxx`)
- это **не** строгая гарантия “ID обязательно был получен из API ранее”

### 3) Action verification (перед финальным ответом)

Для задач, где ожидается мутация (например, логирование часов, апдейт зарплаты/статуса), есть логика, которая:
- проверяет историю вызовов (`call_history`)
- блокирует `ok_answer`, если обязательная мутация не была вызвана

Важно: это эвристика по ключевым словам в тексте задачи и типам вызванных инструментов.

---

## Runtime / Performance

### Параллелизм

Запуск задач реализован через `ThreadPoolExecutor` (по умолчанию 5 воркеров). Каждый task выполняется в своём потоке.

### Rate limiting (как в коде)

В коде используется **не token-bucket**, а простая схема “минимальный интервал между запросами” (shared, thread-safe):
- каждый worker перед стартом задачи вызывает `rate_limiter.acquire()`
- acquire обеспечивает `min_interval = 1/rps` между вызовами

То есть это ближе к “leaky bucket / fixed-interval throttling”, чем к полноценному token bucket.

### Модель/провайдер (as configured)

Модель по умолчанию (и “qwen” alias):
- `qwen/qwen3-235b-a22b-2507`

Провайдер в OpenRouter “фиксируется” через provider preference:
- `{"order": ["Cerebras"], "allow_fallbacks": False}`

Также в конфиге есть:
- `x-ai/grok-4.1-fast`, `x-ai/grok-4.1`, `openai/gpt-4.1`

---

## Частые неточности в “красивых описаниях” (и как в реальности тут)

- **“Scratch сбрасывается каждый шаг”**  
  На практике в коде хранится “хвост” последнего `scratch` (`[-500:]`). Он обновляется каждый шаг, но “сброс” как отдельная операция не делается.

- **“Memory хранит критические ошибки”**  
  Ошибки добавляются в `memory` как `ERR[...]`, но при сжатии `compress_memory()` строки с `"ERR"` выкидываются. Поэтому “ошибки в памяти” — не гарантия.

- **“ID строго проверяется на то, что он был получен из API”**  
  Проверка ID — эвристическая (на “явно фейковые” паттерны), а не проверка “ID ∈ ранее увиденные”.

- **“Token-bucket rate limiting”**  
  Реально — фиксированный интервал между вызовами (см. выше).

---

## 🇬🇧 English Version

### Overview

This agent is implemented as a **single-agent OODA loop (Observe → Orient → Decide → Act)** using the ERC3 SDK (`erc3`) and an OpenAI-compatible client (`openai`) via OpenRouter.

The core is the Structured Outputs Pydantic schema `NextStep`, forcing the model to emit a structured step on every iteration:

1. **think** — concise reasoning (1–2 sentences)
2. **scratch** — working notes (the code keeps only the last 500 characters)
3. **memory** — confirmed facts/IDs (stored as a string and continuously compressed)
4. **function** — the tool call (typed ERC3 request models)

### The OODA Loop (as implemented)

1. **Observe**: fetch identity/context via `who_am_i()`, read `TaskInfo`
2. **Orient**: build a dynamic system prompt (`build_system_prompt`), embed search rules, assemble step context (memory/scratch/IDs)
3. **Decide**: call LLM with `client.beta.chat.completions.parse(..., response_format=NextStep, ...)`
4. **Act**: execute `api.dispatch(fn)`, classify errors, update memory/scratch, track loops

The loop ends on `Req_ProvideAgentResponse`, `done=true`, or `MAX_STEPS` exhaustion.

### Hybrid Guardrails

- **Pre-generation**: guest access denial, regex deny-list, vague/unsupported fast paths
- **Anti-hallucination**: heuristic ID blocking for obviously fake patterns (not “seen-in-API” validation)
- **Action verification**: blocks `ok_answer` when a required mutation wasn’t called (heuristic, keyword-driven)

### Runtime

- **Parallelism**: `ThreadPoolExecutor` (default 5 workers)
- **Rate limiting**: fixed minimum interval throttling (not a full token bucket)
- **Default model**: `qwen/qwen3-235b-a22b-2507` with OpenRouter provider preference `Cerebras`


