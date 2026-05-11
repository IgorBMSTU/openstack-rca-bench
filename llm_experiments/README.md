# LLM Experiments for OpenStack RCA Dataset

## Quick Start

### 1. Install dependencies

```bash
pip install -r llm_experiments/requirements.txt
```

### 2. Set environment variables for your provider

**Qwen (local, default):**
```bash
export OPENAI_API_KEY="dummy"
export OPENAI_API_BASE="<your-llm-endpoint>/v1"
export OPENAI_BASE_URL="<your-llm-endpoint>/v1"
```

Or use provider-specific vars:
```bash
export QWEN_API_KEY="dummy"
export QWEN_BASE_URL="<your-llm-endpoint>/v1"
```

**DeepSeek:**
```bash
export DEEPSEEK_API_KEY="your-key"
```

**GLM:**
```bash
export GLM_API_KEY="your-key"
```

**Kimi:**
```bash
export KIMI_API_KEY="your-key"
```

### 3. Dry run (test pipeline without LLM calls)

```bash
cd <project_root>
python3 -m llm_experiments.src.run_experiment --dry-run --limit 2
```

### 4. Run experiment on Qwen (2 incidents for quick test)

```bash
python3 -m llm_experiments.src.run_experiment \
  --provider qwen \
  --model qwen3-coder-30b-a3b \
  --prompt-strategy zero_shot \
  --log-strategy hybrid \
  --limit 2
```

### 5. Run full evaluation on all 64 incidents

```bash
python3 -m llm_experiments.src.run_experiment \
  --provider qwen \
  --model qwen3-coder-30b-a3b \
  --prompt-strategy zero_shot \
  --log-strategy hybrid \
  --max-log-chars 60000
```

### 6. Resume interrupted experiment

```bash
python3 -m llm_experiments.src.run_experiment \
  --provider qwen \
  --experiment-name qwen_qwen3-coder-30b-a3b_zero_shot_20260501_200500 \
  --resume
```

## Available Strategies

### Prompt strategies
- `zero_shot` — logs only
- `with_context` — logs + known component list + injection time
- `chain_of_thought` — ask model to reason step by step

### Log reduction strategies
- `full` — all logs
- `error_only` — ERROR/CRITICAL/FATAL lines only
- `around_injection` — ±2 minutes around injection time
- `truncated` — last N entries
- `hybrid` — ERROR lines + around injection (recommended, default)

## Results

Results are saved to `llm_experiments/results/{experiment_name}/`:
- `config.json` — experiment configuration
- `predictions.jsonl` — one line per incident with prediction and evaluation
- `metrics.json` — aggregated accuracy metrics
- `summary.txt` — human-readable summary

LLM responses are cached in `llm_experiments/results/cache/` to avoid duplicate API calls.

## Project Structure

```
llm_experiments/
├── src/
│   ├── dataset_loader.py   # Load incidents from rca-framework/incidents/
│   ├── llm_client.py       # Unified OpenAI-compatible client with retry + cache
│   ├── prompt_builder.py   # Build prompts with different strategies
│   ├── evaluator.py        # Compare predictions to ground truth
│   ├── results_store.py    # Save/load experiment results
│   └── run_experiment.py   # Main orchestrator
├── prompts/
│   └── system_rca.txt      # System prompt for OpenStack RCA
├── results/                # Experiment outputs + cache
└── requirements.txt
```
