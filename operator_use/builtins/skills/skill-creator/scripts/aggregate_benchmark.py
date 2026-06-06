#!/usr/bin/env python3
"""
Aggregate benchmark results from test runs.

Usage:
    python -m scripts.aggregate_benchmark /path/to/iteration-N --skill-name skill-name
"""

import json
import sys
from pathlib import Path
from collections import defaultdict
import statistics

def aggregate_benchmark(workspace_path, skill_name):
    """Aggregate grading.json files from all eval runs into a benchmark."""
    workspace = Path(workspace_path)
    
    configurations = defaultdict(lambda: {"results": []})
    
    # Find all eval directories
    for eval_dir in sorted(workspace.glob("eval-*")):
        if not eval_dir.is_dir():
            continue
        
        eval_id = eval_dir.name
        
        # Check with_skill and without_skill (or old_skill) subdirectories
        for config_dir in eval_dir.glob("*_skill"):
            config_name = config_dir.parent.name.replace("eval-", "")
            
            grading_file = config_dir / "grading.json"
            timing_file = config_dir / "timing.json"
            
            if not grading_file.exists():
                continue
            
            with open(grading_file) as f:
                grading = json.load(f)
            
            timing = {}
            if timing_file.exists():
                with open(timing_file) as f:
                    timing = json.load(f)
            
            # Calculate pass rate
            expectations = grading.get("expectations", [])
            if expectations:
                passed = sum(1 for e in expectations if e.get("passed"))
                pass_rate = passed / len(expectations)
            else:
                pass_rate = 0.0
            
            result = {
                "eval_id": eval_id,
                "pass_rate": pass_rate,
                "total_tokens": timing.get("total_tokens", 0),
                "duration_ms": timing.get("duration_ms", 0)
            }
            
            configurations[config_name]["results"].append(result)
    
    # Calculate aggregates
    benchmark = {"configurations": []}
    
    for config_name, config_data in sorted(configurations.items()):
        results = config_data["results"]
        
        if not results:
            continue
        
        pass_rates = [r["pass_rate"] for r in results]
        tokens = [r["total_tokens"] for r in results if r["total_tokens"] > 0]
        durations = [r["duration_ms"] for r in results if r["duration_ms"] > 0]
        
        agg = {
            "name": config_name,
            "pass_rate_mean": statistics.mean(pass_rates),
            "pass_rate_stddev": statistics.stdev(pass_rates) if len(pass_rates) > 1 else 0,
        }
        
        if tokens:
            agg["total_tokens_mean"] = statistics.mean(tokens)
            agg["total_tokens_stddev"] = statistics.stdev(tokens) if len(tokens) > 1 else 0
        
        if durations:
            agg["duration_ms_mean"] = statistics.mean(durations)
            agg["duration_ms_stddev"] = statistics.stdev(durations) if len(durations) > 1 else 0
        
        agg["results"] = results
        benchmark["configurations"].append(agg)
    
    # Save benchmark.json
    benchmark_file = workspace / "benchmark.json"
    with open(benchmark_file, "w") as f:
        json.dump(benchmark, f, indent=2)
    
    print(f"✅ Benchmark saved to {benchmark_file}")
    print(json.dumps(benchmark, indent=2))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.aggregate_benchmark /path/to/iteration-N --skill-name skill-name")
        sys.exit(1)
    
    workspace = sys.argv[1]
    skill_name = "unnamed-skill"
    
    if "--skill-name" in sys.argv:
        idx = sys.argv.index("--skill-name")
        if idx + 1 < len(sys.argv):
            skill_name = sys.argv[idx + 1]
    
    aggregate_benchmark(workspace, skill_name)
