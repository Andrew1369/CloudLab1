"""PARCS Solver module: coordinator benchmark and stateful worker operations."""
import json
import os
from pathlib import Path
from time import perf_counter
from datetime import datetime, timezone

import numpy as np
from Pyro4 import expose
from threadpoolctl import threadpool_limits

from gauss_lab.algorithm import (generate_system, pack, unpack, eliminate_rows,
                                 solve_sequential, solution_metrics)
from gauss_lab.distributed import RemotePool, solve_parallel
from gauss_lab.environment import machine_info


@expose
class Solver:
    def __init__(self, workers=None, input_file_name=None, output_file_name=None):
        self.workers = workers or []
        self.input_file_name = input_file_name
        self.output_file_name = output_file_name
        self.cancel_requested = lambda: False
        self.indices = None

    def identity(self):
        return machine_info()

    def load_rows(self, indices, rows, rhs):
        self.indices = np.asarray(indices, dtype=np.int64)
        self.rows, self.rhs = unpack(rows), unpack(rhs)
        if self.rows.ndim != 2 or len(self.rows) != len(self.indices) or self.rhs.shape != (len(self.indices),):
            raise ValueError("Invalid row block")
        self.lookup = {int(index): local for local, index in enumerate(self.indices)}
        return len(indices)

    def pivot_candidate(self, k):
        active = np.flatnonzero(self.indices >= k)
        if not len(active):
            return None
        local = int(active[np.argmax(np.abs(self.rows[active, k]))])
        return dict(index=int(self.indices[local]), magnitude=float(abs(self.rows[local, k])),
                    row=pack(self.rows[local]), rhs=float(self.rhs[local]))

    def get_row(self, index):
        local = self.lookup[index]
        return dict(row=pack(self.rows[local]), rhs=float(self.rhs[local]))

    def put_row(self, index, row, rhs):
        local = self.lookup[index]
        self.rows[local], self.rhs[local] = unpack(row), rhs
        return True

    def eliminate(self, k, pivot_row, pivot_rhs):
        eliminate_rows(self.rows, self.rhs, self.indices, k, unpack(pivot_row), pivot_rhs)
        return True

    def gather_rows(self):
        return dict(indices=self.indices.tolist(), rows=pack(self.rows), rhs=pack(self.rhs))

    def solve(self):
        config = json.loads(Path(self.input_file_name).read_text(encoding="utf-8"))
        counts = config.get("worker_counts", [1, 2, 4])
        sizes = config.get("sizes", [500, 1000, 2000])
        repeats, warmups = config.get("repeats", 5), config.get("warmups", 1)
        if (not isinstance(repeats, int) or repeats < 1 or not isinstance(warmups, int) or warmups < 0
                or not counts or not sizes or any(not isinstance(p, int) or p < 1 for p in counts)
                or len(set(counts)) != len(counts)
                or any(not isinstance(n, int) or n < max(counts) for n in sizes)
                or max(counts) > len(self.workers)):
            raise ValueError("Invalid configuration or insufficient registered workers")
        seed = int(config.get("seed", 42))
        all_uris = [str(worker._pyroUri) for worker in self.workers]
        pools = {p: RemotePool(all_uris[:p]) for p in counts}
        output = dict(schema_version=1, created_at=datetime.now(timezone.utc).isoformat(),
                      config=config, coordinator=machine_info(), workers=[], runs=[], cases=[],
                      environment="google-cloud" if os.environ.get("GAUSS_CLOUD") == "gcp" else "local",
                      timing="Input copies, row distribution, pivot exchange, elimination, gather, back substitution. "
                             "Excludes cluster/module startup, generation, validation and file writes.")
        try:
            output["workers"] = pools[max(counts)].all("identity", [()] * max(counts))
            if any(w["algorithm_sha256"] != output["coordinator"]["algorithm_sha256"] for w in output["workers"]):
                raise RuntimeError("Coordinator and worker algorithm versions differ")
            with threadpool_limits(limits=1):
                # Optional explicit systems exercise row swaps on real remote workers.
                for case in config.get("cases", []):
                    a, b = np.asarray(case["matrix"], dtype=float), np.asarray(case["rhs"], dtype=float)
                    expected = solve_sequential(a, b)
                    for p in counts:
                        x = solve_parallel(a, b, pools[p], self.cancel_requested)
                        metrics = solution_metrics(a, b, x, expected)
                        output["cases"].append(dict(name=case["name"], workers=p, solution=x.tolist(), **metrics))
                for n in sizes:
                    a, b, expected = generate_system(n, seed)
                    variants = [0] + counts

                    def run_variant(p):
                        return solve_parallel(a, b, pools[p], self.cancel_requested) if p else solve_sequential(a, b, self.cancel_requested)

                    for _ in range(warmups):
                        for p in variants:
                            solution_metrics(a, b, run_variant(p), expected)
                    for repeat in range(1, repeats + 1):
                        # Rotate execution order to reduce consistent warm-cache/order bias.
                        offset = (repeat - 1) % len(variants)
                        for p in variants[offset:] + variants[:offset]:
                            started = perf_counter()
                            x = run_variant(p)
                            seconds = perf_counter() - started
                            metrics = solution_metrics(a, b, x, expected)
                            output["runs"].append(dict(algorithm="parcs" if p else "sequential", n=n,
                                workers=p, seed=seed, repeat=repeat, seconds=seconds, **metrics))
        finally:
            for pool in pools.values():
                pool.close()
        destination = Path(self.output_file_name)
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(destination)
