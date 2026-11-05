"""Persistent cyclic row distribution via actual PARCS Pyro worker proxies."""
from concurrent.futures import ThreadPoolExecutor
import threading

import numpy as np
import Pyro4

from .algorithm import validate_system, pivot_tolerance, pack, unpack, back_substitute


class RemotePool:
    def __init__(self, uris):
        self.uris = list(uris)
        self.executor = ThreadPoolExecutor(max_workers=len(self.uris))
        self.local = threading.local()
        self.proxies = []
        self.lock = threading.Lock()

    def call(self, worker, method, *args):
        proxies = getattr(self.local, "proxies", None)
        if proxies is None:
            proxies = self.local.proxies = {}
        if worker not in proxies:
            proxy = Pyro4.Proxy(self.uris[worker])
            proxy._pyroSerializer = "serpent"
            proxy._pyroTimeout = 60
            proxies[worker] = proxy
            with self.lock:
                self.proxies.append(proxy)
        return getattr(proxies[worker], method)(*args)

    def all(self, method, arguments):
        futures = [self.executor.submit(self.call, i, method, *args)
                   for i, args in enumerate(arguments)]
        return [future.result() for future in futures]

    def one(self, worker, method, *args):
        return self.executor.submit(self.call, worker, method, *args).result()

    def close(self):
        self.executor.shutdown(wait=True)
        for proxy in self.proxies:
            proxy._pyroRelease()


def solve_parallel(matrix, rhs, pool, cancel=None):
    a, b = validate_system(matrix, rhs)
    n, p = len(b), len(pool.uris)
    if not 1 <= p <= n:
        raise ValueError("Workers must be between 1 and matrix size")
    tolerance = pivot_tolerance(a)
    pool.all("load_rows", [(list(range(w, n, p)), pack(a[w::p]), pack(b[w::p])) for w in range(p)])
    for k in range(n):
        if cancel and cancel():
            raise RuntimeError("Job cancelled")
        candidates = pool.all("pivot_candidate", [(k,)] * p)
        candidates = [c for c in candidates if c is not None]
        best = min(candidates, key=lambda c: (-c["magnitude"], c["index"]))
        if best["magnitude"] <= tolerance:
            raise ValueError("Matrix is singular or numerically ill-conditioned")
        pivot_index = best["index"]
        pivot_row, pivot_rhs = unpack(best["row"]), best["rhs"]
        if pivot_index != k:
            # Slots retain their global indices; exchange their contents.
            previous = pool.one(k % p, "get_row", k)
            pool.one(k % p, "put_row", k, best["row"], pivot_rhs)
            pool.one(pivot_index % p, "put_row", pivot_index, previous["row"], previous["rhs"])
        # Submit all calls before waiting so worker computations overlap.
        pool.all("eliminate", [(k, pack(pivot_row), pivot_rhs)] * p)
    blocks = pool.all("gather_rows", [()] * p)
    for block in blocks:
        indices = block["indices"]
        a[indices], b[indices] = unpack(block["rows"]), unpack(block["rhs"])
    return back_substitute(a, b)
