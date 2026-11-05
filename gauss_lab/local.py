"""Context manager for real master/worker subprocesses."""
from contextlib import AbstractContextManager
import os
from pathlib import Path
import socket
import subprocess
import sys
from time import monotonic, sleep

import requests


def reserve_ports(count):
    sockets = []
    try:
        for _ in range(count):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


class LocalCluster(AbstractContextManager):
    def __init__(self, workers=4, log_dir="results/local-logs"):
        self.workers = workers
        self.log_dir = Path(log_dir)
        self.processes, self.logs = [], []
        self.root = Path(__file__).resolve().parents[1]

    def __enter__(self):
        if self.workers < 1:
            raise ValueError("At least one worker is required")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        ports = reserve_ports(1 + 2 * self.workers)
        self.master = f"http://127.0.0.1:{ports[0]}"
        commands = [["master", "--port", str(ports[0])]]
        for i in range(self.workers):
            commands.append(["worker", "--port", str(ports[1 + i]), "--rpc-port", str(ports[1 + self.workers + i]),
                             "--master", f"127.0.0.1:{ports[0]}"])
        try:
            for i, args in enumerate(commands):
                stream = (self.log_dir / f"node-{i}.log").open("w", encoding="utf-8")
                self.logs.append(stream)
                env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
                           PARCS_JOB_ROOT=str((self.log_dir / "jobs").resolve()))
                env.pop("GAUSS_CLOUD", None)
                self.processes.append(subprocess.Popen([sys.executable, "start_node.py", *args], cwd=self.root,
                    env=env, stdout=stream, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
            deadline = monotonic() + 45
            while monotonic() < deadline:
                if any(p.poll() is not None for p in self.processes):
                    raise RuntimeError(f"Node exited during startup. See {self.log_dir}")
                try:
                    response = requests.get(self.master + "/api/worker", timeout=1)
                    if response.ok and len(response.json()["workers"]) == self.workers:
                        return self
                except requests.RequestException:
                    pass
                sleep(0.25)
            raise TimeoutError(f"Workers did not register. See {self.log_dir}")
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        for process in reversed(self.processes):
            if process.poll() is None:
                if os.name == "nt":
                    # Windows venv launchers may have an actual Python child process.
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    process.terminate()
        for process in self.processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in self.logs:
            stream.close()
