"""Google Cloud helper. Deployment/deletion print a plan unless --apply is supplied.

Only the resources recorded in this project's manifest are managed. No cloud
resources are created by the local benchmark or by importing this module.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent


def command(args, capture=False):
    print("+ " + shlex.join(str(a) for a in args), flush=True)
    result = subprocess.run([str(a) for a in args], check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else None


def gc(project, *args, capture=False):
    binary = "gcloud.cmd" if os.name == "nt" else "gcloud"
    return command([binary, *args, "--project", project], capture=capture)


def identifier(value):
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,19}", value) or value.endswith("-"):
        raise argparse.ArgumentTypeError("Use 2-20 lowercase letters/digits/hyphens, starting with a letter")
    return value


def plan(project, zone, prefix, workers, machine_type):
    token = uuid.uuid4().hex[:16]
    return dict(schema_version=1, project=project, zone=zone, region=zone.rsplit("-", 1)[0],
                prefix=prefix, token=token, machine_type=machine_type,
                network=f"{prefix}-vpc", subnet=f"{prefix}-subnet",
                firewall=[f"{prefix}-internal", f"{prefix}-iap-ssh"],
                instances=[f"{prefix}-master"] + [f"{prefix}-worker-{i}" for i in range(1, workers + 1)],
                worker_count=workers, created_at=datetime.now(timezone.utc).isoformat(),
                status="planned", touched=[])


def save(manifest, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def package(destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    excluded = {".venv", ".git", "__pycache__", "results", "dist", ".cloud", ".pytest_cache"}
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob("*")):
            relative = path.relative_to(ROOT)
            if path.is_file() and not (set(relative.parts) & excluded) and path.suffix != ".pyc":
                archive.write(path, relative.as_posix())
    return destination


def deploy(args):
    manifest = plan(args.project, args.zone, args.prefix, args.workers, args.machine_type)
    print(json.dumps(manifest, indent=2))
    print("All VMs use the same type. PARCS ports are private; SSH uses IAP.")
    print("VMs automatically STOP after 4 hours; disks remain billable until cleanup.")
    if not args.apply:
        print("PLAN ONLY. Add --apply to create billable resources after reviewing console prices/quotas.")
        return
    if args.manifest.exists():
        raise RuntimeError("Manifest already exists. Reuse it for run/status/destroy or select another manifest.")
    p, z = args.project, args.zone
    gc(p, "services", "enable", "compute.googleapis.com", "iap.googleapis.com")
    # Never reuse a pre-existing network or VM: the helper owns only new resources.
    networks = json.loads(gc(p, "compute", "networks", "list", "--format=json", capture=True))
    instances = json.loads(gc(p, "compute", "instances", "list", "--format=json", capture=True))
    firewalls = json.loads(gc(p, "compute", "firewall-rules", "list", "--format=json", capture=True))
    subnets = json.loads(gc(p, "compute", "networks", "subnets", "list", "--format=json", capture=True))
    if (any(n["name"] == manifest["network"] for n in networks)
            or any(i["name"] in manifest["instances"] for i in instances)
            or any(f["name"] in manifest["firewall"] for f in firewalls)
            or any(s["name"] == manifest["subnet"] for s in subnets)):
        raise RuntimeError("A planned resource name already exists; choose a different --prefix")
    manifest["status"] = "deploying"
    save(manifest, args.manifest)

    def create(kind, name, *parameters):
        # Record attempts as well as successes so interrupted deployment is recoverable.
        manifest["touched"].append([kind, name])
        save(manifest, args.manifest)
        gc(p, "compute", kind, "create", name, *parameters, "--quiet")

    create("networks", manifest["network"], "--subnet-mode=custom", "--description", manifest["token"])
    manifest["touched"].append(["subnets", manifest["subnet"]])
    save(manifest, args.manifest)
    gc(p, "compute", "networks", "subnets", "create", manifest["subnet"], "--network", manifest["network"],
       "--region", manifest["region"], "--range=10.42.0.0/24", "--description", manifest["token"], "--quiet")
    create("firewall-rules", manifest["firewall"][0], "--network", manifest["network"],
           "--allow=tcp:8080,tcp:8081,tcp:9081", "--source-tags", args.prefix, "--target-tags", args.prefix,
           "--description", manifest["token"])
    create("firewall-rules", manifest["firewall"][1], "--network", manifest["network"],
           "--allow=tcp:22", "--source-ranges=35.235.240.0/20", "--target-tags", args.prefix,
           "--description", manifest["token"])
    for name in manifest["instances"]:
        create("instances", name, "--zone", z, "--machine-type", args.machine_type,
               "--subnet", manifest["subnet"], "--tags", args.prefix,
               "--labels", f"gauss-owner={manifest['token']}",
               "--image-family=debian-12", "--image-project=debian-cloud", "--boot-disk-size=10GB",
               "--boot-disk-type=pd-standard", "--boot-disk-auto-delete", "--no-service-account", "--no-scopes",
               "--max-run-duration=14400s", "--instance-termination-action=STOP")
    configure_cluster(manifest, args.manifest)


def configure_cluster(manifest, manifest_path):
    """Install/update PARCS on this manifest's existing VMs; create no resources."""
    p, z = manifest["project"], manifest["zone"]
    ips = {}
    for name in manifest["instances"]:
        info = json.loads(gc(p, "compute", "instances", "describe", name, "--zone", z, "--format=json", capture=True))
        if info.get("labels", {}).get("gauss-owner") != manifest["token"]:
            raise RuntimeError(f"Ownership check failed for {name}; setup stopped")
        if info.get("status") != "RUNNING":
            raise RuntimeError(f"{name} is not RUNNING. Start this existing VM before resuming")
        ips[name] = info["networkInterfaces"][0]["networkIP"]
    manifest["ips"] = ips
    manifest["status"] = "configuring"
    save(manifest, manifest_path)
    archive = package(ROOT / "dist" / "gauss-project.zip")
    master_ip = ips[manifest["instances"][0]]
    for i, name in enumerate(manifest["instances"]):
        ssh(manifest, name, "mkdir -p ~/gauss-lab")
        gc(p, "compute", "scp", str(archive), f"{name}:~/gauss-lab/project.zip", "--zone", z, "--tunnel-through-iap", "--quiet")
        role, port = ("master", 8080) if i == 0 else ("worker", 8081)
        service = shlex.join([".venv/bin/python", "tools/install_service.py", "--role", role,
                              "--host", ips[name], "--port", str(port), "--rpc-port", "9081",
                              "--master", f"{master_ip}:8080"])
        bootstrap = ("sudo env DEBIAN_FRONTEND=noninteractive apt-get update && "
                     "sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv unzip && "
                     "cd ~/gauss-lab && unzip -qo project.zip && python3 -m venv .venv && "
                     ".venv/bin/python -m pip install -r requirements.txt && " + service)
        ssh(manifest, name, bootstrap)
    manifest["status"] = "ready"
    save(manifest, manifest_path)
    print("Cluster ready. Run smoke before full measurements; keep this manifest for cleanup.")


def resume(args):
    manifest = load(args.manifest)
    if manifest.get("status") == "deleted":
        raise RuntimeError("This cluster was deleted; it cannot be resumed")
    print("Resuming setup on existing VMs; no VMs or networks will be created.")
    configure_cluster(manifest, args.manifest)


def ssh(manifest, instance, remote):
    gc(manifest["project"], "compute", "ssh", instance, "--zone", manifest["zone"],
       "--tunnel-through-iap", "--quiet", "--command", remote)


def load(path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not re.fullmatch(r"[0-9a-f]{16}", manifest.get("token", "")):
        raise ValueError("Invalid manifest")
    prefix = identifier(manifest["prefix"])
    expected = plan(manifest["project"], manifest["zone"], prefix, manifest["worker_count"], manifest["machine_type"])
    for key in ("instances", "network", "subnet", "firewall"):
        if manifest[key] != expected[key]:
            raise ValueError(f"Unexpected resource names in manifest: {key}")
    return manifest


def run(args):
    m = load(args.manifest)
    if m["status"] != "ready":
        raise RuntimeError("Cluster is not ready; inspect the manifest and deployment logs")
    config = json.loads((ROOT / "configs" / f"{args.config}.json").read_text(encoding="utf-8"))
    if max(config["worker_counts"]) > m["worker_count"]:
        raise RuntimeError("This config requests more workers than the deployed cluster")
    remote = shlex.join([".venv/bin/python", "run_benchmark.py", "--master",
                        f"http://{m['ips'][m['instances'][0]]}:8080", "--config", f"configs/{args.config}.json",
                        "--output", "results/gcp"])
    wait = shlex.join([".venv/bin/python", "tools/wait_cluster.py", "--master",
                       f"http://{m['ips'][m['instances'][0]]}:8080", "--workers", str(m["worker_count"])])
    ssh(m, m["instances"][0], "cd ~/gauss-lab && " + wait + " && " + remote)
    args.output.mkdir(parents=True, exist_ok=True)
    gc(m["project"], "compute", "scp", "--recurse", f"{m['instances'][0]}:~/gauss-lab/results/gcp",
       str(args.output), "--zone", m["zone"], "--tunnel-through-iap", "--quiet")


def destroy(args):
    m = load(args.manifest)
    print("Cleanup plan:", json.dumps({key: m[key] for key in ("project", "zone", "instances", "network", "subnet", "firewall")}, indent=2))
    if not args.apply:
        print("PLAN ONLY. Save experiment outputs first, then add --apply to delete these lab resources.")
        return
    p = m["project"]
    # Verify ownership before any deletion, including partially created clusters.
    inventory = json.loads(gc(p, "compute", "instances", "list", "--format=json", capture=True))
    existing = [i for i in inventory if i["name"] in m["instances"]]
    for instance in existing:
        if instance.get("labels", {}).get("gauss-owner") != m["token"] or instance["zone"].rsplit("/", 1)[-1] != m["zone"]:
            raise RuntimeError("VM ownership check failed; no resources deleted")
    networks = json.loads(gc(p, "compute", "networks", "list", "--format=json", capture=True))
    for network in networks:
        if network["name"] == m["network"] and network.get("description") != m["token"]:
            raise RuntimeError("Network ownership check failed; no resources deleted")
    firewalls = json.loads(gc(p, "compute", "firewall-rules", "list", "--format=json", capture=True))
    owned_fw = [f for f in firewalls if f["name"] in m["firewall"]]
    if any(f.get("description") != m["token"] for f in owned_fw):
        raise RuntimeError("Firewall ownership check failed; no resources deleted")
    subnets = json.loads(gc(p, "compute", "networks", "subnets", "list", "--format=json", capture=True))
    owned_subnet = [s for s in subnets if s["name"] == m["subnet"] and s["region"].endswith("/" + m["region"])]
    if any(s.get("description") != m["token"] for s in owned_subnet):
        raise RuntimeError("Subnet ownership check failed; no resources deleted")
    for instance in existing:
        gc(p, "compute", "instances", "delete", instance["name"], "--zone", m["zone"], "--quiet")
    for firewall in owned_fw:
        gc(p, "compute", "firewall-rules", "delete", firewall["name"], "--quiet")
    for subnet in owned_subnet:
        gc(p, "compute", "networks", "subnets", "delete", subnet["name"], "--region", m["region"], "--quiet")
    if any(n["name"] == m["network"] for n in networks):
        gc(p, "compute", "networks", "delete", m["network"], "--quiet")
    m["status"] = "deleted"
    save(m, args.manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / ".cloud" / "cluster.json")
    commands = parser.add_subparsers(dest="action", required=True)
    deploy_parser = commands.add_parser("deploy")
    deploy_parser.add_argument("--project", required=True)
    deploy_parser.add_argument("--zone", default="europe-west1-b")
    deploy_parser.add_argument("--prefix", type=identifier, default="gauss-lab")
    deploy_parser.add_argument("--workers", type=int, choices=[1, 2, 4], default=4)
    deploy_parser.add_argument("--machine-type", default="e2-standard-2")
    deploy_parser.add_argument("--apply", action="store_true")
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--config", choices=["smoke", "local_demo", "cloud"], default="smoke")
    run_parser.add_argument("--output", type=Path, default=ROOT / "results" / "downloaded")
    commands.add_parser("resume", help="Resume software setup on existing VMs")
    commands.add_parser("status")
    destroy_parser = commands.add_parser("destroy")
    destroy_parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.action == "deploy":
        deploy(args)
    elif args.action == "resume":
        resume(args)
    elif args.action == "run":
        run(args)
    elif args.action == "destroy":
        destroy(args)
    else:
        m = load(args.manifest)
        gc(m["project"], "compute", "instances", "list", "--filter", f"labels.gauss-owner={m['token']}",
           "--format=table(name,status,machineType.basename(),zone.basename())")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
