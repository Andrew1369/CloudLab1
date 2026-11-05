import argparse
import json
from pathlib import Path

from gauss_lab.reporting import write_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Regenerate CSV, charts and summary from a saved PARCS result")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_results(json.loads(args.input.read_text(encoding="utf-8")), args.output)
