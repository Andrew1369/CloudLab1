"""Install and validate only this lab's systemd service on a Linux VM."""
import argparse
import getpass
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def unit_text(root, executable, user, args):
    root = str(root)
    if not root.startswith("/") or any(c in root for c in "\r\n"):
        raise ValueError("The Linux working directory must be an absolute path")
    # WorkingDirectory is a single path, not an ExecStart-style quoted argument.
    working_directory = root.replace("%", "%%")
    exec_args = [str(executable), root + "/start_node.py", args.role, "--host", args.host,
                 "--port", str(args.port), "--rpc-port", str(args.rpc_port), "--master", args.master]
    quoted_args = [json.dumps(arg.replace("%", "%%").replace("$", "$$"), ensure_ascii=False) for arg in exec_args]
    job_environment = json.dumps("PARCS_JOB_ROOT=" + working_directory + "/results/jobs", ensure_ascii=False)
    return "\n".join([
        "[Unit]", "Description=Gaussian elimination PARCS node", "After=network-online.target", "Wants=network-online.target",
        "", "[Service]", f"User={user}", f"WorkingDirectory={working_directory}",
        "Environment=PYTHONUNBUFFERED=1", "Environment=PYTHONDONTWRITEBYTECODE=1", "Environment=GAUSS_CLOUD=gcp",
        "Environment=" + job_environment, "ExecStart=" + " ".join(quoted_args),
        "Restart=on-failure", "RestartSec=3", "", "[Install]", "WantedBy=multi-user.target", "",
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=["master", "worker"], required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--rpc-port", type=int, default=9081)
    parser.add_argument("--master", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    unit = unit_text(root, sys.executable, getpass.getuser(), args)
    # Check unit syntax before replacing the installed service definition.
    with tempfile.TemporaryDirectory(prefix="gauss-unit-") as directory:
        candidate = Path(directory) / "gauss-parcs.service"
        candidate.write_text(unit, encoding="utf-8")
        subprocess.run(["systemd-analyze", "verify", str(candidate)], check=True)
    subprocess.run(["sudo", "tee", "/etc/systemd/system/gauss-parcs.service"], input=unit, text=True, check=True,
                   stdout=subprocess.DEVNULL)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "systemctl", "reset-failed", "gauss-parcs.service"], check=False)
    subprocess.run(["sudo", "systemctl", "enable", "gauss-parcs.service"], check=True)
    try:
        subprocess.run(["sudo", "systemctl", "restart", "gauss-parcs.service"], check=True)
        subprocess.run(["sudo", "systemctl", "is-active", "gauss-parcs.service"], check=True)
    except subprocess.CalledProcessError:
        subprocess.run(["sudo", "journalctl", "-u", "gauss-parcs.service", "-n", "40", "--no-pager"], check=False)
        raise


if __name__ == "__main__":
    main()
