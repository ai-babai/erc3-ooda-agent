# OODA Agent ERC

OODA Loop Agent для ERC3 challenge — самодостаточный подпроект.

## Описание

Высокопроизводительный одноагентный агент для ERC3 challenge, реализующий архитектуру **OODA Loop (Observe-Orient-Decide-Act)** с гибридными защитными механизмами (Hybrid Guardrails). Использует Structured Outputs через OpenAI-совместимый API (OpenRouter) для выполнения бизнес-задач с минимальным количеством ошибок.

## Требования

- Python 3.10+
- Переменные окружения:
  - `OPENROUTER_API_KEY` (обязательно) — API ключ для OpenRouter
  - Опционально: `AGNO_MAX_STEPS`, `AGNO_MAX_COMPLETION_TOKENS`, `AGNO_MAX_WORKERS`, `AGNO_RATE_LIMIT_RPS`
  - Опционально: `OPENROUTER_BASE_URL` (по умолчанию: `https://openrouter.ai/api/v1`)

### Настройка переменных окружения

**Вариант 1: Создать файл `.env` (рекомендуется)**

Создайте файл `.env` в директории `ooda_agent_erc`:

```bash
cd ooda_agent_erc
echo "OPENROUTER_API_KEY=your_api_key_here" > .env
```

Или вручную создайте файл `.env` со следующим содержимым:

```
OPENROUTER_API_KEY=your_api_key_here
```

**Вариант 2: Экспортировать переменную окружения**

```bash
export OPENROUTER_API_KEY=your_api_key_here
python main.py
```

**Вариант 3: Установить в текущей сессии**

```bash
OPENROUTER_API_KEY=your_api_key_here python main.py
```

## Установка

```bash
pip install -r requirements.txt
```

Или с использованием conda:

```bash
conda create -n ooda-agent python=3.10
conda activate ooda-agent
pip install -r requirements.txt
```

## Использование

### Базовый запуск

**Вариант 1: Запуск из директории проекта (рекомендуется)**

```bash
cd ooda_agent_erc
python main.py
```

**Вариант 2: Запуск как модуль из родительской директории**

```bash
# Из родительской директории
python -m ooda_agent_erc.main
```

### Параметры командной строки

- `-b, --benchmark` — выбор бенчмарка: `dev`, `test`, `prod` (по умолчанию: `dev`)
- `-t, --task` — выбор конкретных задач (можно указать несколько раз):
  - По индексу: `-t 1 -t 3`
  - По task_id или spec_id: `-t add_time_entry_me`
- `-m, --model` — выбор модели (алиасы: `grok-fast`, `grok`, `qwen`, `gpt`)
- `-w, --workers` — количество параллельных воркеров (по умолчанию: 5)
- `--sequential` — принудительный последовательный запуск

### Примеры

**При запуске из директории `ooda_agent_erc`:**

```bash
# Запуск с конкретными задачами
python main.py -t 1 -t 3 -t add_time_entry_me

# Использование конкретной модели
python main.py -m grok-fast

# Последовательный запуск
python main.py --sequential

# Запуск на production бенчмарке
python main.py -b prod -w 3
```

**При запуске как модуль из родительской директории:**

```bash
python -m ooda_agent_erc.main -m qwen -t 1 -t 2
python -m ooda_agent_erc.main -b test --sequential
```

## Структура проекта

```
ooda_agent_erc/
├── __init__.py          # Пакетный маркер
├── agent.py             # Основная логика агента
├── config.py            # Конфигурация (модели, лимиты)
├── dispatcher.py        # Дополнительные схемы запросов
├── json_logging.py      # JSON логирование
├── load_env.py          # Загрузка переменных окружения
├── main.py              # Точка входа
├── trace.py              # Трейсинг и логирование
├── requirements.txt     # Зависимости
├── README.md            # Документация
└── task_logs/           # Логи задач
    ├── errors/          # Логи ошибок
    ├── sessions/        # Логи сессий
    └── tasks/            # Логи отдельных задач
```

## Архитектура / Architecture

<details>
<summary><strong>🇷🇺 Русская версия</strong></summary>

### Обзор архитектуры

Реализована высокопроизводительная одноагентная архитектура на основе принципа **OODA Loop (Observe-Orient-Decide-Act)** с использованием Structured Outputs.

В основе лежит Pydantic-схема `NextStep`, которая заставляет модель генерировать структурированный "поток мыслей" на каждом шаге:

1. **Think**: Краткое рассуждение (1-2 предложения)
2. **Scratch**: "Черновик" для гипотез поиска и заметок (ограничен 500 символами, обновляется каждый шаг)
3. **Memory**: Подтвержденные факты и ID. Этот лог сжимается автоматически, сохраняя только последние 12 важных записей из последних 20 строк. Важные записи содержат валидные ID (`proj_...`, `emp_...`, `cust_...`), подтвержденные факты и критические ошибки
4. **Function**: Непосредственный вызов инструмента (1:1 маппинг с ERC3 API)

### Ключевые особенности

#### 1. Динамический контекст и "Search Ladders"

Системный промпт собирается динамически на основе роли пользователя (Guest vs Authenticated) и содержит **28 критических правил** (нумерованных от 0 до 28), извлеченных из wiki и практического опыта. Главное нововведение — **"Search Ladders"**: алгоритмические инструкции по поиску с явными fallback-стратегиями, встроенные прямо в промпт.

Примеры Search Ladders:
- Если поиск по `skills+location` возвращает пустой результат → попробуй поиск только по `location` → затем проверь навыки вручную через `GetEmployee`
- Если поиск проекта по имени клиента не дает результатов → попробуй ключевые слова из задачи (например, "route scenario lab")
- Если локация "Danmark" не работает → попробуй "Denmark" или "DK" → затем отбрось фильтр локации и фильтруй вручную
- Если проект "Data Foundations Audit" не найден → попробуй "Foundation" → "data" → название компании (например, "rhinesteel")

#### 2. Гибридные защитные механизмы (Hybrid Guardrails)

Многоуровневая защита от ошибок до, во время и после генерации:

**Pre-Generation (до генерации):**
- Мгновенная блокировка через regex-паттерны запросов о зарплатах (`DENY_PATTERNS`)
- Блокировка попыток вайпа данных и попыток гостей получить доступ к внутренней информации
- Блокировка неопределенных запросов через `VAGUE_PATTERNS` ("that cool project", "which one?")
- Блокировка неподдерживаемых функций через `UNSUPPORTED_PATTERNS`

**Anti-Hallucination (во время выполнения):**
- Валидатор `_looks_hallucinated()` перехватывает вызовы инструментов перед отправкой в API
- Блокирует вымышленные ID (например, `proj_105`, `emp_1`, `emp_john`) которые не соответствуют реальным паттернам ERC3
- Требует использования только реальных ID из ответов API (например, `proj_scandifoods_packaging_cv_poc`, `ana_kovac`)

**Action Verification (проверка действий):**
- Агент программно не может ответить "Done" (`outcome="ok_answer"`), если задача требовала изменения данных, но соответствующий API не был вызван
- Логика проверяет `call_history` и блокирует финальный ответ до выполнения обязательных мутаций (`Req_LogTimeEntry`, `Req_UpdateEmployeeInfo`, `Req_UpdateProjectStatus`)
- После 3 блокировок агент получает критическое предупреждение с требованием немедленно выполнить действие

#### 3. Исполнение и производительность

**Runtime:**
- Собственный раннер на Python с `ThreadPoolExecutor` (5 потоков по умолчанию, настраивается через `-w`)
- Общий Rate Limiter с токен-бакетом для контроля скорости запросов (по умолчанию 3.0 RPS на воркер)
- Поддержка последовательного (`--sequential`) и параллельного выполнения
- Thread-safe логирование с блокировками для консольного вывода

**Модель:**
- Оптимизировано под **Qwen 2.5 (235B)** через Cerebras для оптимального соотношения скорость/стоимость
- Fallback на **GPT-4.1** и **Grok 4.1** при необходимости (настраивается через `MODEL_OPTIONS`)
- Поддержка кастомных провайдеров через OpenRouter с `extra_body` параметрами

**Метрики производительности:**
- ~103 задачи за 15 минут (на бенчмарке dev/test)
- Стоимость: ~$0.60 за прогон (зависит от модели и количества задач)
- Средняя глубина: 5-8 шагов на задачу
- Максимальная глубина: 30 шагов (настраивается через `AGNO_MAX_STEPS`)

### OODA Loop в действии

1. **Observe**: Получение задачи и контекста через ERC3 API (`who_am_i()`, информация о пользователе, текущая дата)
2. **Orient**: Анализ задачи, построение динамического системного промпта с учетом роли, истории и правил поиска
3. **Decide**: LLM генерирует структурированный `NextStep` с полями `think`, `scratch`, `memory`, `plan`, `actions_done`, `filters_tried` и `function`
4. **Act**: Выполнение действия через ERC3 API (`api.dispatch()`), обработка ошибок (`ApiException`), обновление контекста и памяти

Цикл повторяется до достижения `done=true` в `NextStep`, вызова `Req_ProvideAgentResponse` или исчерпания лимита шагов (`MAX_STEPS`).

</details>

<details>
<summary><strong>🇬🇧 English Version</strong></summary>

### Architecture Overview

A high-performance single-agent architecture based on the **OODA Loop (Observe-Orient-Decide-Act)** principle using Structured Outputs.

The core is the Pydantic schema `NextStep`, which forces the model to output distinct cognitive steps in every single turn:

1. **Think**: Concise reasoning (1-2 sentences)
2. **Scratch**: Working notes, search hypotheses, and disambiguation logic (limited to 500 characters, updated each step)
3. **Memory**: Confirmed facts and IDs. This is an append-only log that gets compressed automatically, retaining only the last 12 important entries from the last 20 lines. Important entries contain valid IDs (`proj_...`, `emp_...`, `cust_...`), confirmed facts, and critical errors
4. **Function**: The actual tool invocation (1:1 API mapping with ERC3)

### Key Features

#### 1. Dynamic Context & "Search Ladders"

The system prompt is dynamically assembled based on the user's role (Guest vs Authenticated) and contains **28 critical rules** (numbered 0-28), distilled from wiki and practical experience. The main innovation is **"Search Ladders"**: algorithmic search instructions with explicit fallback strategies, embedded directly in the prompt.

Search Ladder examples:
- If `skills+location` search returns empty → try `location` only → then verify skills manually via `GetEmployee`
- If project search by customer name fails → try project keywords from task (e.g., "route scenario lab")
- If location "Danmark" doesn't work → try "Denmark" or "DK" → then drop location filter and filter manually
- If project "Data Foundations Audit" not found → try "Foundation" → "data" → company name (e.g., "rhinesteel")

#### 2. Hybrid Guardrails

Multi-layer defense system prevents errors before, during, and after generation:

**Pre-Generation:**
- Instant regex-based rejection of salary queries (`DENY_PATTERNS`)
- Blocks data wipe attempts and guest access to internal data
- Blocks vague queries via `VAGUE_PATTERNS` ("that cool project", "which one?")
- Blocks unsupported features via `UNSUPPORTED_PATTERNS`

**Anti-Hallucination:**
- Validator `_looks_hallucinated()` intercepts tool calls before API dispatch
- Blocks hallucinated IDs (e.g., `proj_105`, `emp_1`, `emp_john`) that don't match real ERC3 patterns
- Requires using only real IDs from API responses (e.g., `proj_scandifoods_packaging_cv_poc`, `ana_kovac`)

**Action Verification:**
- Agent cannot programmatically respond "Done" (`outcome="ok_answer"`) if task required data mutation but corresponding API wasn't called
- Logic checks `call_history` and blocks final response until required mutations are executed (`Req_LogTimeEntry`, `Req_UpdateEmployeeInfo`, `Req_UpdateProjectStatus`)
- After 3 blocks, agent receives critical warning demanding immediate action execution

#### 3. Execution & Performance

**Runtime:**
- Custom Python runner with `ThreadPoolExecutor` (5 workers by default, configurable via `-w`)
- Shared token-bucket Rate Limiter for request rate control (default 3.0 RPS per worker)
- Supports sequential (`--sequential`) and parallel execution
- Thread-safe logging with locks for console output

**Model:**
- Optimized for **Qwen 2.5 (235B)** via Cerebras for optimal speed/cost ratio
- Fallback to **GPT-4.1** and **Grok 4.1** when needed (configurable via `MODEL_OPTIONS`)
- Supports custom providers via OpenRouter with `extra_body` parameters

**Performance Metrics:**
- ~103 tasks in 15 minutes (on dev/test benchmark)
- Cost: ~$0.60 per run (depends on model and task count)
- Average depth: 5-8 steps per task
- Maximum depth: 30 steps (configurable via `AGNO_MAX_STEPS`)

### OODA Loop in Action

1. **Observe**: Get task and context via ERC3 API (`who_am_i()`, user info, current date)
2. **Orient**: Analyze task, build dynamic system prompt considering role, history, and search rules
3. **Decide**: LLM generates structured `NextStep` with `think`, `scratch`, `memory`, `plan`, `actions_done`, `filters_tried`, and `function` fields
4. **Act**: Execute action via ERC3 API (`api.dispatch()`), handle errors (`ApiException`), update context and memory

The loop repeats until `done=true` in `NextStep`, `Req_ProvideAgentResponse` is called, or step limit (`MAX_STEPS`) is exhausted.

</details>

## Логирование

Агент создает три типа логов:
1. **JSONL логи** — в `logs/ooda_agent_erc/` (по task_id)
2. **JSON логи задач** — в `task_logs/tasks/`
3. **JSON логи сессий** — в `task_logs/sessions/`
4. **Текстовые логи ошибок** — в `task_logs/errors/`

## Поддерживаемые модели

- `qwen/qwen3-235b-a22b-2507` (по умолчанию)
- `x-ai/grok-4.1-fast`
- `x-ai/grok-4.1`
- `openai/gpt-4.1`

## Технические детали

### Ограничения API

- Все операции Search/List имеют жесткий лимит `limit=5` (системное ограничение ERC3)
- Пагинация: `offset=0→5→10...` с `limit=5` всегда
- Попытки запросить `limit > 5` автоматически исправляются на 5

### Управление памятью / Memory Management

- **Memory** сжимается автоматически функцией `compress_memory()`, сохраняя только последние 12 важных записей из последних 20 строк
- Важные записи определяются по наличию ключевых префиксов: ID (`proj_...`, `emp_...`, `cust_...`), стрелок (`→`), ключевых слов (`salary`, `logged`, `updated`)
- Ошибки (`ERR`) автоматически исключаются из сжатой памяти
- **Scratch** ограничен последними 500 символами (`scratch[-500:]`) и обновляется каждый шаг

### Обработка ошибок / Error Handling

Ошибки классифицируются автоматически функцией `_classify_error()` на основе текста исключения:

- **permission**: доступ запрещен → `outcome="denied_security"` и немедленный возврат
  - Ключевые слова: "permission", "denied", "unauthorized", "forbidden", "not allowed", "only lead", "not a member"
- **system**: системная ошибка → `outcome="error_internal"` и остановка выполнения
  - Ключевые слова: "internal server error", "500", "503", "502", "timeout", "page limit exceeded"
  - После 3 системных ошибок (`api_fails >= 3`) агент прекращает работу
- **not_found**: сущность не найдена → обычно `outcome="ok_answer"` с объяснением
  - Исключение: для операций изменения (`Req_UpdateProjectStatus`, `Req_UpdateProjectTeam`) → `outcome="denied_security"`
- **other**: прочие ошибки → добавляются в память и контекст для следующего шага

Ошибки записываются в память в формате `ERR[type]: {error_message}` и используются для улучшения следующих шагов.

## Лицензия

Проект готов к публикации как отдельный GitHub репозиторий.

