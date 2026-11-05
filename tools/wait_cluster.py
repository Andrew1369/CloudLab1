import argparse
from time import monotonic, sleep
import requests


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", required=True)
    parser.add_argument("--workers", type=int, required=True)
    args = parser.parse_args()
    deadline = monotonic() + 90
    while monotonic() < deadline:
        try:
            response = requests.get(args.master.rstrip("/") + "/api/worker", timeout=3)
            response.raise_for_status()
            if len(response.json()["workers"]) == args.workers:
                print("All PARCS workers registered")
                break
        except requests.RequestException:
            pass
        sleep(1)
    else:
        raise SystemExit("Workers did not register within 90 seconds; inspect node logs")
