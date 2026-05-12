# 1
git clone https://github.com/IgorBMSTU/openstack-rca-bench.git 

# 2
python3 scripts/validate_dataset.py

# 3
python3 framework/baselines/evaluate_table3.py

# 4 
cat rca-framework/incidents/INC-2026-006/metadata.json | head -20

# 5
export DEEPSEEK_API_KEY="sk-YOUR_KEY_HERE"
python3 -m llm_experiments.src.run_experiment --provider deepseek --model deepseek-v4-flash --prompt-strategy multi_agent --log-strategy hybrid --offset 2 --limit 1

# 6 
cat llm_experiments/results/*/predictions.jsonl | python3 -m json.tool



 export DEEPSEEK_API_KEY="sk-524a453193874509b045a863fe2b4122"
python3 -m llm_experiments.src.run_experiment \
  --provider deepseek \
  --model deepseek-v4-flash \
  --prompt-strategy multi_agent \
  --log-strategy hybrid \
  --offset 2 \
  --limit 1


#
git clone https://github.com/IgorBMSTU/openstack-rca-bench.git
cd openstack-rca-bench

#
python3 scripts/validate_dataset.py

#
python3 framework/baselines/evaluate_table3.py

#
export DEEPSEEK_API_KEY="sk-..."
python3 -m llm_experiments.src.run_experiment \
  --provider deepseek \
  --model deepseek-v4-flash \
  --prompt-strategy multi_agent \
  --log-strategy hybrid \
  --offset 2 \
  --limit 1

#
cat llm_experiments/results/*/predictions.jsonl | python3 -m json.tool
