import argparse
import json
import os
from pathlib import Path

for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

from gauss_lab.client import submit
from gauss_lab.local import LocalCluster
from gauss_lab.reporting import write_results


def main():
    parser = argparse.ArgumentParser(description="Benchmark sequential Gauss and actual PARCS workers")
    parser.add_argument("--master", help="Existing master URL. Omit to start a temporary local cluster")
    parser.add_argument("--config", type=Path, default=Path("configs/smoke.json"))
    parser.add_argument("--output", type=Path, default=Path("results/local"))
    parser.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.master:
        result = submit(args.master, config, args.timeout)
    else:
        with LocalCluster(max(config["worker_counts"]), args.output / "logs") as cluster:
            print(f"PARCS ready: {cluster.master}", flush=True)
            result = submit(cluster.master, config, args.timeout)
    write_results(result, args.output)
    print(f"Validated {len(result['runs'])} runs. Results: {args.output.resolve()}")


if __name__ == "__main__":
    main()
