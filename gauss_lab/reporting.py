import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import median


def summarize(runs):
    grouped = defaultdict(list)
    for run in runs:
        grouped[(run["n"], run["algorithm"], run["workers"])].append(run)
    baseline = {n: median(r["seconds"] for r in rows)
                for (n, algorithm, p), rows in grouped.items() if algorithm == "sequential"}
    summary = []
    for (n, algorithm, p), rows in sorted(grouped.items()):
        seconds = median(r["seconds"] for r in rows)
        speedup = baseline[n] / seconds
        summary.append(dict(n=n, algorithm=algorithm, workers=p, repeats=len(rows), median_seconds=seconds,
                            speedup=speedup, efficiency=speedup / p if p else None,
                            max_relative_residual=max(r["relative_residual"] for r in rows),
                            max_solution_error=max(r["max_solution_error"] or 0 for r in rows)))
    return summary


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_results(result, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "raw.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(destination / "runs.csv", result["runs"])
    summary = summarize(result["runs"])
    write_csv(destination / "summary.csv", summary)
    lines = [f"# Gaussian elimination: {result['environment']}", "",
             "Local measurements are not Google Cloud measurements.", "",
             "| n | Algorithm | Workers | Median, s | Speedup | Efficiency |",
             "|---:|---|---:|---:|---:|---:|"]
    for row in summary:
        efficiency = f"{row['efficiency']:.4f}" if row["efficiency"] is not None else "-"
        lines.append(f"| {row['n']} | {row['algorithm']} | {row['workers']} | {row['median_seconds']:.6f} | {row['speedup']:.4f} | {efficiency} |")
    lines += ["", "## Timing", "", result["timing"], "",
              "Startup/upload time is outside the measured solver interval. Input copies, distribution,",
              "serialization, all synchronization, final gather and back substitution are inside it.", "",
              "## Environment", "", "See raw.json for actual coordinator/worker hostnames, process IDs,",
              "Python/NumPy/BLAS versions and Google Compute Engine metadata when available.", "",
              "Speedup below 1 is an observed slowdown and is not filtered out."]
    (destination / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    make_plots(summary, destination, result["environment"])


def make_plots(summary, destination, environment):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sizes = sorted({r["n"] for r in summary})
    fig, ax = plt.subplots(figsize=(9, 5))
    variants = sorted({(r["algorithm"], r["workers"]) for r in summary}, key=lambda x: (x[1], x[0]))
    for algorithm, workers in variants:
        rows = sorted([r for r in summary if (r["algorithm"], r["workers"]) == (algorithm, workers)], key=lambda r: r["n"])
        ax.plot([r["n"] for r in rows], [r["median_seconds"] for r in rows], marker="o",
                label="Sequential" if not workers else f"PARCS, {workers} workers")
    ax.set(xlabel="Matrix size n", ylabel="Median wall time, seconds", title=f"Execution time ({environment})")
    ax.set_yscale("log")
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(destination / "time.png", dpi=180)
    plt.close(fig)
    for metric, label in [("speedup", "Speedup T_seq / T_p"), ("efficiency", "Efficiency S_p / p")]:
        fig, ax = plt.subplots(figsize=(8, 5))
        for n in sizes:
            rows = sorted([r for r in summary if r["n"] == n and r["workers"]], key=lambda r: r["workers"])
            ax.plot([r["workers"] for r in rows], [r[metric] for r in rows], marker="o", label=f"n={n}")
        counts = sorted({r["workers"] for r in summary if r["workers"]})
        if metric == "speedup":
            ax.plot(counts, counts, "--", color="grey", label="Ideal S=p")
        else:
            ax.axhline(1, linestyle="--", color="grey", label="Ideal E=1")
        ax.set(xlabel="PARCS workers p", ylabel=label, title=f"{label} ({environment})", xticks=counts)
        ax.grid(True, alpha=0.25)
        ax.legend()
        fig.tight_layout()
        fig.savefig(destination / f"{metric}.png", dpi=180)
        plt.close(fig)
