# OpenStack-RCA-Bench
A Reproducible IaaS Root Cause Analysis Dataset for OpenStack Environments

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg) ![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-brightgreen.svg) ![ASE 2026 Dataset](https://img.shields.io/badge/ASE-2026_Dataset-orange)

An open dataset of 64 chaos-engineering incidents for benchmarking RCA methods in OpenStack IaaS, featuring multi-agent LLM reasoning traces and consensus-failure analysis.

**Version:** 1.0.0 | **Incidents:** 64 | **Scenarios:** 46 | **Log Entries:** ~145,000

## Quick Start

Dataset is already in the repo (64 incidents). No cluster needed.

### Quick Demo with Docker (API key required)

```bash
git clone https://github.com/IgorBMSTU/openstack-rca-bench
cd openstack-rca-bench
export QWEN_API_KEY="sk-..."
docker compose up
```

Builds environment, validates dataset integrity, runs multi-agent RCA on 2 sample incidents.  
**Expected output:** `ALL CHECKS PASSED` with predictions for 2 incidents (exit 0).
**API key:** Set `QWEN_API_KEY` (any non-empty value) and `QWEN_BASE_URL` to point to your model endpoint (e.g. `http://your-server:8000/v1`). For DeepSeek, use `DEEPSEEK_API_KEY` instead — see below.

---

### Full LLM evaluation (API key required)

```bash
# 1. Validate all 64 incidents
python3 scripts/validate_dataset.py

# 2. Run experiment
export QWEN_API_KEY="sk-..."
python3 -m llm_experiments.src.run_experiment \
  --provider qwen \
  --model qwen3-coder-30b-a3b \
  --prompt-strategy multi_agent \
  --log-strategy hybrid
```
**API keys:** Local model — set `QWEN_API_KEY` (any value) and `QWEN_BASE_URL` to your endpoint (e.g. `http://localhost:8000/v1` for Ollama/vLLM). For DeepSeek Cloud, set `DEEPSEEK_API_KEY` from [DeepSeek Platform](https://platform.deepseek.com/).

**Output:** `llm_experiments/results/<name>/predictions.jsonl`  
**To reproduce paper (78.12%):** use `--prompt-strategy multi_agent` with Qwen or DeepSeek.

---

### Extending the dataset (requires OpenStack cluster)

```bash
python3 orchestrator/single_incident_orchestrator.py \
  --scenario nova_compute-stop \
  --target 10.197.75.20 \
  --duration 120
```

Injects fault, collects logs, runs sanity pre/post.  
**Output:** `rca-framework/incidents/INC-2026-XXX/` with `metadata.json`, `raw_logs.json.gz`, pre/post sanity.

## Benchmark Results (from the paper)

| Approach | Provider | Model | Incidents | Top-1 Acc. |
| :--- | :--- | :--- | :--- | :--- |
| Zero-shot | Qwen | qwen3-coder-30b-a3b | 64 | **15.6%** |
| Zero-shot | DeepSeek | deepseek-v4-flash | 64 | **10.9%** |
| **Multi-agent** | Qwen | qwen3-coder-30b-a3b | 64 | **78.12%** |
| **Multi-agent** | DeepSeek | deepseek-v4-flash | 64 | **78.12%** |

**Key insight:** Both models converge on the same 50 correct predictions out of 64. The bottleneck shifts from LLM reasoning to dependency graph completeness. Detailed aggregation analysis reveals a Reliability Paradox: agreement among agents is not a proxy for correctness, collapsing to 14.3% on consensus.

## Dataset Overview

| Metric | Value |
| :--- | :--- |
| Total incidents | 64 |
| Unique fault scenarios | 46 |
| Hosts (nodes) | **7** (1 undercloud, 1 controller, 2 compute, 3 storage) |
| Fault types | 4 (service-stop, port-block, process-kill, config-corruption) |
| Log entries | ~145,000 total (~2,268 per incident on average) |
| Pre/post sanity pass rate | 100% (all 64 incidents verified) |
openstack-rca-bench/
<details>
<summary><b>Directory Structure</b></summary>

```
openstack-rca-dataset/
├── rca-framework/incidents/   # 64 incident directories (metadata + logs)
├── framework/                 # Fault injection framework (injector, collectors)
├── orchestrator/              # End-to-end incident orchestration
├── sanity_checks/             # Cluster health checks
├── llm_experiments/           # LLM-based RCA evaluation pipeline
│   └── src/multi_agent/       # 3-agent neuro-symbolic orchestrator
├── config/                    # Cluster topology and service mappings
└── scripts/                   # Validation and automation scripts
```
</details>

<details>
<summary><b>Ground Truth and Validation</b></summary>

Every incident is validated using a three-level approach:

1. **Injection verification** — The injector confirms the service was stopped (or port blocked, etc.)
2. **Log-level signatures** — Expected error patterns are verified against collected logs
3. **Sanity checks** — Cluster health verified before and after (100% pass rate)

Sample `metadata.json`:
```json
{
  "incident_id": "INC-2026-002",
  "scenario": "nova_compute-stop",
  "injection": {
    "service": "nova_compute",
    "injection_time": "2026-04-21T11:02:15.108587",
    "status": "success"
  },
  "validation": {
    "signatures_expected": ["nova-compute", "stop", "ERROR", "down"],
    "match_rate": 1.0
  }
}
```
</details>


<details>
<summary><b>Service Categories Covered</b></summary>

| Category | Incidents | Examples |
|----------|-----------|---------|
| Compute (Nova) | 16 | nova-api, nova-compute, nova-scheduler, nova-conductor |
| OVN Networking | 15 | ovn-controller, ovn-metadata-agent, ovn-northd |
| Ceph Storage | 5 | ceph-osd-0/1/2, ceph-mon, ceph-mgr |
| Network (Neutron) | 3 | neutron-api, neutron-dhcp |
| Storage (Cinder) | 3 | cinder-api, cinder-scheduler, cinder-volume |
| Image (Glance) | 3 | glance-api, glance-api-internal |
| Identity (Keystone) | 3 | keystone (stop, port-block, config-corruption) |
| Orchestration (Heat) | 3 | heat-api, heat-engine, heat-api-cfn |
| Database (MySQL) | 2 | mysql-stop, mysql-crash |
| Messaging (RabbitMQ) | 2 | rabbitmq-stop, rabbitmq-crash |
| Dashboard (Skyline) | 2 | skyline-apiserver, skyline-console |
| Monitoring | 2 | prometheus-stop, grafana-stop |
| Storage (iSCSI) | 2 | iscsid-stop |
| Other | 2 | placement-api, haproxy, redis |
</details>

<details>
<summary><b>Full Directory Structure</b></summary>

```
openstack-rca-bench/
├── README.md                              # This file
├── LICENSE                                # MIT License
├── Makefile                               # Validation targets
├── setup.sh                               # One-command setup
├── requirements.txt                       # Root dependencies
│
├── rca-framework/                         # Incident data
│   └── incidents/                         # 64 incident directories
│       ├── INC-2026-002/
│       │   ├── metadata.json              # Ground truth + injection metadata
│       │   ├── pre_sanity.json            # Cluster health before injection
│       │   ├── post_sanity.json           # Cluster health after recovery
│       │   └── raw_logs.json.gz           # Gzipped systemd-journal logs
│       └── ... (64 total)
│
├── framework/                             # Fault injection framework
│   ├── injector/
│   │   └── injector_v3_podman.py          # Podman/systemd fault injector
│   ├── collector/
│   │   ├── collector.py                   # Loki-based log collector
│   │   ├── simple_collector.py            # SSH-based log collector
│   │   └── loki_collector.py              # Loki query collector
│   ├── baselines/
│   │   └── evaluate.py                    # Rule-based + LLM baseline evaluation
│   └── realistic_runner.py                # End-to-end incident runner
│
├── orchestrator/                          # Incident orchestration
│   ├── single_incident_orchestrator.py
│   ├── run_write_incident.py
│   ├── workload_generator.py
│   └── loki_exporter.py
│
├── sanity_checks/                         # Cluster health checks
│   ├── sanity_checks.py
│   ├── run_sanity_checks.py
│   └── test_connectivity.py
│
├── llm_experiments/                       # LLM-based RCA evaluation
│   ├── src/
│   │   ├── dataset_loader.py
│   │   ├── llm_client.py
│   │   ├── prompt_builder.py
│   │   ├── evaluator.py
│   │   ├── results_store.py
│   │   ├── run_experiment.py
│   │   └── multi_agent/
│   │       ├── orchestrator.py            # 3-agent neuro-symbolic orchestrator
│   │       ├── prompts.py
│   │       ├── preprocessor.py
│   │       └── graph.py
│   ├── prompts/
│   │   └── system_rca.txt
│   └── results/                           # Experiment results
│
├── config/
│   ├── infrastructure.json                # Cluster topology and service mappings
│   └── realistic_config.json
│
└── scripts/
    ├── validate_dataset.py                # Dataset integrity validation
    └── run_all_experiments.sh
```
</details>

<details>
<summary><b>Cluster Environment</b></summary>

The dataset was collected on a 7-node OpenStack Wallaby cluster:

| Component | Details |
|-----------|---------|
| OpenStack version | Wallaby |
| Deployment method | TripleO |
| Container runtime | podman |
| Controller nodes | 1 |
| Compute nodes | 2 |
| Storage nodes | 3 |
| High-availability | Pacemaker + Galera + HAProxy |
| SDN | OVN / OVS with FRRouting (BGP) |
| Log collection | systemd-journal + Loki |
</details>

## Citation

```bibtex
@inproceedings{openstackrca2026,
  title={OpenStack-RCA-Bench: A Reproducible IaaS Root Cause Analysis Dataset},
  author={Igor Bogomolov and Oleg Borisenko},
  booktitle={ASE 2026 — Tools and Datasets Track},
  year={2026}
}
```

## License

MIT License — see LICENSE for details.
