"""Python-3 port of upstream scheduler: upload Solver, obtain Pyro proxies, solve.

Jobs remain serialized. Failed worker initialization fails the entire job instead
of silently benchmarking fewer workers. Cancellation is cooperative.
"""
import logging
from threading import Thread

import Pyro4
import requests

from .file_utils import get_solution_path, get_output_path, get_input_path
from .module_loader import load_module

log = logging.getLogger("Job Scheduler")


class Scheduler(Thread):
    def __init__(self, master, scheduled_jobs):
        super().__init__(daemon=True)
        self.master = master
        self.job_home = master.conf.job_home
        self.scheduled_jobs = scheduled_jobs
        self.current_job = None

    def run(self):
        while True:
            job = self.scheduled_jobs.get()
            selected = []
            self.current_job = job
            try:
                if job.aborted:
                    continue
                job.start_job()
                proxies = []
                for worker in sorted(list(self.master.workers), key=lambda w: w.id):
                    if not worker.enabled:
                        continue
                    selected.append(worker)
                    with open(get_solution_path(self.job_home, job.id), "rb") as source:
                        response = requests.post(
                            f"http://{worker.ip}:{worker.port}/api/internal/job",
                            files={"solution": source}, data={"job_id": job.id}, timeout=60,
                        )
                    response.raise_for_status()
                    uri = response.json()["uri"]
                    if not uri.startswith("PYRO:"):
                        raise RuntimeError(f"Invalid worker RPC URI: {uri}")
                    proxies.append(Pyro4.Proxy(uri))
                module = load_module(f"solver_module_{job.id}", get_solution_path(self.job_home, job.id))
                solver = module.Solver(proxies, get_input_path(self.job_home, job.id), get_output_path(self.job_home, job.id))
                solver.cancel_requested = lambda: job.aborted
                solver.solve()
                if job.aborted:
                    job.abort_job()
                else:
                    job.end_job()
            except Exception as error:
                log.exception("Job %s failed", job.id)
                if job.aborted:
                    job.abort_job()
                else:
                    job.end_job(True, str(error))
            finally:
                for worker in selected:
                    try:
                        response = requests.delete(
                            f"http://{worker.ip}:{worker.port}/api/internal/rpc/{job.id}", timeout=30,
                        )
                        response.raise_for_status()
                    except requests.RequestException:
                        log.exception("Could not release worker %s", worker.id)
                self.current_job = None
                self.scheduled_jobs.task_done()
