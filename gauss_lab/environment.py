import json
import os
import platform
import socket
import sys
import urllib.request
import hashlib
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_info


def machine_info():
    result = dict(hostname=socket.gethostname(), pid=os.getpid(), python=sys.version.split()[0],
                  numpy=np.__version__, platform=platform.platform(), logical_cpus=os.cpu_count(),
                  blas=threadpool_info(), cloud=None)
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for name in ("solution.py", "gauss_lab/algorithm.py", "gauss_lab/distributed.py"):
        digest.update((root / name).read_bytes())
    result["algorithm_sha256"] = digest.hexdigest()
    if os.environ.get("GAUSS_CLOUD") == "gcp":
        cloud = {}
        for key in ("name", "id", "zone", "machine-type"):
            req = urllib.request.Request(
                f"http://169.254.169.254/computeMetadata/v1/instance/{key}",
                headers={"Metadata-Flavor": "Google"})
            try:
                with urllib.request.urlopen(req, timeout=2) as response:
                    cloud[key] = response.read().decode("utf-8")
            except OSError as error:
                cloud[key] = f"unavailable: {type(error).__name__}"
        result["cloud"] = cloud
    return result
