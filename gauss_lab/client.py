import json
from pathlib import Path
from time import monotonic, sleep

import requests


def submit(master, config, timeout=3600):
    """Submit through PARCS's normal HTTP job API and retrieve its output."""
    master = master.rstrip("/")
    solution = Path(__file__).resolve().parents[1] / "solution.py"
    with solution.open("rb") as source:
        response = requests.post(master + "/api/job", files={
            "solution_file": ("solution.py", source, "text/x-python"),
            "input_file": ("input.json", json.dumps(config).encode(), "application/json"),
        }, data={"job_name": "Gauss benchmark"}, timeout=30)
    response.raise_for_status()
    job_id = response.json()["job_id"]
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        response = requests.get(master + "/api/jobs", timeout=15)
        response.raise_for_status()
        job = next(j for j in response.json()["jobs"] if j["id"] == job_id)
        if job["finished"]:
            if job["failed"]:
                raise RuntimeError(f"PARCS job {job_id} failed: {job['status']}")
            response = requests.get(f"{master}/api/job/{job_id}/output", timeout=60)
            response.raise_for_status()
            result = response.json()
            result["job_id"] = job_id
            result["job_wall_seconds"] = job["duration"]
            return result
        sleep(0.25)
    # Avoid leaving an unobserved job running after a client timeout.
    requests.delete(f"{master}/api/job/{job_id}", timeout=15).raise_for_status()
    raise TimeoutError(f"PARCS job {job_id} exceeded {timeout}s; cancellation requested")
