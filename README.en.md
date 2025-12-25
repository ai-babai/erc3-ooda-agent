# OODA Agent ERC

An experimental agent for the ERC3 challenge focused on maximizing inference speed and end-to-end task throughput.

Implements an **OODA Loop (Observe-Orient-Decide-Act)** architecture with **Cerebras** (via OpenRouter) to minimize latency.

## Results & Metrics

This agent is not aiming for perfect accuracy — its main feature is fast response time.

**Speed Leaderboard:** #4 ([table](https://erc.timetoact-group.at/assets/erc3.html#speed))
- **Time per task:** ~10s
- **Cost:** ~$0.34
- **Score:** ~0.350

**Locality Leaderboard:** #5 ([table](https://erc.timetoact-group.at/assets/erc3.html#locality))
- **Time:** ~11s
- **Score:** ~0.320

*Note: The agent is optimized for fast execution of simple and medium scenarios. In complex cases it may hallucinate or make logic mistakes in favor of speed.*

## Key Features

- **Cerebras inference (preferred):** Default model is `qwen/qwen3-235b-a22b-2507` (OpenRouter provider preference: Cerebras), optimized for high token throughput.
- **OODA architecture:** Clear separation into observe, orient, decide, and act phases on every step.
- **Structured Outputs:** Pydantic-enforced response structure (think, scratch, memory, function call).
- **Aggressive context management:** Memory is continuously compressed to keep only the most important IDs and facts.

## Requirements

- Python 3.10+
- **OpenRouter API Key** (required)

## Installation

```bash
pip install -r requirements.txt
```

Create a `.env` file:

```bash
OPENROUTER_API_KEY=your_key_here
```

## Usage

### Quick start

Run the agent (multithreading enabled by default):

```bash
# From the project directory
python main.py
```

### CLI parameters

- `-b, --benchmark`: `dev` (default), `test`, `prod`.
- `-t, --task`: Run specific tasks (e.g. `-t 1 -t add_time_entry`).
- `-m, --model`: Model selection. Default is `qwen` (Cerebras preferred). Also available: `grok`, `grok-fast`, `gpt`.
- `-w, --workers`: Number of worker threads (default: 5).
- `--sequential`: Sequential mode (useful for debugging).

Examples:

```bash
# Run a single task on the fast model
python main.py -t add_time_entry_me -m qwen

# Sequential run for easier debugging
python main.py --sequential
```

## Architecture (details)

The project uses an OODA loop to make decisions:

1. **Observe:** Fetch data via the API.
2. **Orient:** Build a dynamic prompt. The prompt includes search heuristics ("Search Ladders") — if an exact search fails, the agent tries partial matches (e.g., "Danmark" -> "DK").
3. **Decide:** Generate the next step.
4. **Act:** Call the API.

### Guardrails (Simple Guardrails)

To reduce hallucinations at high speed:

- **Regex filters:** Block salary questions and “wipe data” requests before calling the LLM.
- **ID check (heuristic):** Before dispatching a tool call, the agent blocks *obviously fake* ID patterns (e.g., numeric `proj_105`, `emp_1`). This is not a strict “ID must have been previously returned by the API” guarantee — a plausible but invented ID may still pass if it doesn’t match the heuristic patterns.
- **Action verification:** The agent cannot claim “Done” if the task required a mutation but the corresponding API call wasn’t executed.

### Project structure

```
ooda_agent_erc/
├── agent.py             # OODA loop logic
├── config.py            # Model settings
├── main.py              # CLI runner
├── task_logs/           # Execution logs
└── requirements.txt     # Dependencies
```

## Known limitations

1. **Accuracy:** Prioritizing speed may cause missed nuances in complex multi-step tasks.
2. **API limits:** A hard `limit=5` is used for list/search calls.
3. **Memory:** “Long-term memory” is implemented via aggressive log compression; details from many steps earlier may be lost.

## License

MIT


