#!/usr/bin/env bash
# OpenStack RCA LLM Experiments — Automated Runner
# Usage: ./scripts/run_all_experiments.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULTS_DIR="$PROJECT_ROOT/llm_experiments/results"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ---------------------------------------------------------------------------
# 1. Setup check
# ---------------------------------------------------------------------------
check_python() {
    info "Checking Python..."
    if ! command -v python3 &> /dev/null; then
        err "python3 not found. Please install Python 3.8+."
        exit 1
    fi
    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    ok "Python version: $PYTHON_VERSION"
}

check_deps() {
    info "Checking dependencies..."
    cd "$PROJECT_ROOT"
    if ! python3 -c "import openai" 2>/dev/null; then
        warn "openai package not installed. Installing..."
        pip install -r "$PROJECT_ROOT/llm_experiments/requirements.txt"
    fi
    ok "Dependencies OK"
}

# ---------------------------------------------------------------------------
# 2. Environment check
# ---------------------------------------------------------------------------
check_env() {
    info "Checking API keys..."
    local keys=("QWEN_API_KEY" "DEEPSEEK_API_KEY" "GLM_API_KEY" "KIMI_API_KEY" "OPENAI_API_KEY")
    local found=0
    for key in "${keys[@]}"; do
        if [[ -n "${!key:-}" ]]; then
            ok "$key is set"
            ((found++)) || true
        else
            warn "$key is NOT set"
        fi
    done
    if [[ $found -eq 0 ]]; then
        err "No API keys found! Set at least one provider key."
        echo "Example:"
        echo "  export QWEN_API_KEY=\"dummy\""
        echo "  export QWEN_BASE_URL=\"<your-llm-endpoint>/v1\""
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# 3. Phase 1 — Quick test (2 incidents)
# ---------------------------------------------------------------------------
run_quick_test() {
    info "Phase 1: Quick test on 2 incidents..."
    cd "$PROJECT_ROOT"
    python3 -m llm_experiments.src.run_experiment \
        --provider qwen \
        --model qwen3-coder-30b-a3b \
        --limit 2 \
        --log-strategy hybrid \
        --max-log-chars 60000 \
        --experiment-name "quick_test_$(date +%Y%m%d_%H%M%S)"
    ok "Quick test complete. Check llm_experiments/results/quick_test_*/"
}

# ---------------------------------------------------------------------------
# 4. Phase 2 — Full run (all 64)
# ---------------------------------------------------------------------------
run_full() {
    info "Phase 2: Full run on all 64 incidents..."
    info "This will take 1.5-3 hours depending on API latency."
    info "Press ENTER to continue or Ctrl+C to cancel..."
    read -r

    cd "$PROJECT_ROOT"
    local exp_name="qwen_zero_shot_hybrid_$(date +%Y%m%d_%H%M%S)"
    
    python3 -m llm_experiments.src.run_experiment \
        --provider qwen \
        --model qwen3-coder-30b-a3b \
        --prompt-strategy zero_shot \
        --log-strategy hybrid \
        --max-log-chars 60000 \
        --experiment-name "$exp_name"
    
    ok "Full run complete. Results: llm_experiments/results/$exp_name/"
    echo "$exp_name" > "$RESULTS_DIR/last_experiment.txt"
}

# ---------------------------------------------------------------------------
# 5. Phase 3 — Analysis
# ---------------------------------------------------------------------------
run_analysis() {
    info "Phase 3: Generating statistics and visualizations..."
    cd "$PROJECT_ROOT"
    
    info "Dataset statistics..."
    python3 llm_experiments/scripts/dataset_stats.py
    
    info "Figures..."
    python3 llm_experiments/scripts/visualize.py
    
    ok "Analysis complete. Check llm_experiments/results/figures/"
}

# ---------------------------------------------------------------------------
# 6. Resume check
# ---------------------------------------------------------------------------
check_resume() {
    if [[ -f "$RESULTS_DIR/last_experiment.txt" ]]; then
        local last_exp
        last_exp=$(cat "$RESULTS_DIR/last_experiment.txt")
        if [[ -d "$RESULTS_DIR/$last_exp" ]]; then
            warn "Found previous experiment: $last_exp"
            warn "Run with --resume to continue, or delete to start fresh."
            return 0
        fi
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    echo "========================================"
    echo "  OpenStack RCA LLM Experiment Runner"
    echo "========================================"
    echo ""

    check_python
    check_deps
    check_env

    echo ""
    info "Setup complete. Ready to run experiments."
    echo ""

    # Check for resume
    if check_resume; then
        echo ""
        read -rp "Resume last experiment? [y/N] " resume
        if [[ "$resume" =~ ^[Yy]$ ]]; then
            local last_exp
            last_exp=$(cat "$RESULTS_DIR/last_experiment.txt")
            cd "$PROJECT_ROOT"
            python3 -m llm_experiments.src.run_experiment \
                --experiment-name "$last_exp" \
                --resume
            run_analysis
            exit 0
        fi
    fi

    # Quick test
    read -rp "Run quick test (2 incidents)? [Y/n] " quick
    if [[ ! "$quick" =~ ^[Nn]$ ]]; then
        run_quick_test
    fi

    # Full run
    echo ""
    read -rp "Run full experiment (64 incidents)? [y/N] " full
    if [[ "$full" =~ ^[Yy]$ ]]; then
        run_full
        run_analysis
    fi

    echo ""
    ok "All done! Results are in llm_experiments/results/"
}

main "$@"
