"""Run a course PARCS-Python master or worker on Python 3."""
import argparse
import os

for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = "1"

import Pyro4
from parcs_py import parcs, Config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=["master", "worker"])
    parser.add_argument("--host", default="127.0.0.1", help="Local bind/advertised IP; use internal VM IP in GCP")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--rpc-port", type=int)
    parser.add_argument("--master", default="127.0.0.1:8080")
    args = parser.parse_args()
    Pyro4.config.SERIALIZER = "serpent"
    Pyro4.config.SERIALIZERS_ACCEPTED = {"serpent"}
    Pyro4.config.REQUIRE_EXPOSE = True
    # Solver variants may leave a worker connection idle for over a minute.
    # Keep server-side idle connections open; each client call retains its 60s timeout.
    Pyro4.config.COMMTIMEOUT = 0
    Pyro4.config.THREADPOOL_SIZE = 32
    Pyro4.config.MAX_MESSAGE_SIZE = 256 * 1024 * 1024
    host, port = args.master.rsplit(":", 1)
    conf = Config(args.host, args.port,
                  master_ip=host if args.role == "worker" else None,
                  master_port=int(port) if args.role == "worker" else None,
                  rpc_port=args.rpc_port)
    parcs.start(conf)


if __name__ == "__main__":
    main()
