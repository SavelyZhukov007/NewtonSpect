from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
VENV_DIR = BACKEND_DIR / ".venv"
if os.name == "nt":
    VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
else:
    VENV_PYTHON = VENV_DIR / "bin" / "python"


def npm_executable() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def format_cmd(cmd: list[str]) -> str:
    return subprocess.list2cmdline(cmd)


def run_command(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print(f"[run] {cwd}: {format_cmd(cmd)}")
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def require_binary(binary_name: str) -> None:
    if shutil.which(binary_name) is None:
        raise SystemExit(f"Required binary was not found in PATH: {binary_name}")


def ensure_backend_venv() -> None:
    if VENV_PYTHON.exists():
        return
    print("[info] Creating backend virtual environment...")
    run_command([sys.executable, "-m", "venv", str(VENV_DIR)])


def setup_backend() -> None:
    ensure_backend_venv()
    run_command([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"])
    run_command([str(VENV_PYTHON), "-m", "pip", "install", "-r", str(BACKEND_DIR / "requirements.txt")])
    run_command([str(VENV_PYTHON), "-m", "pip", "install", "pytest", "pytest-mock", "httpx"])


def setup_frontend() -> None:
    run_command([npm_executable(), "install"], cwd=FRONTEND_DIR)


def setup_all() -> None:
    require_binary(npm_executable())
    require_binary("ffmpeg")
    setup_backend()
    setup_frontend()


def runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    storage_root = ROOT / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)
    env.setdefault("NEWTONSPECT_STORAGE_ROOT", str(storage_root))
    env.setdefault("NEWTONSPECT_DB_PATH", str(storage_root / "newtonspect.db"))
    env.setdefault("NEWTONSPECT_OPENVINO_MODELS_DIR", str(storage_root / "models" / "openvino"))
    return env


def run_checks(*, include_frontend_lint: bool = True) -> None:
    run_command([str(VENV_PYTHON), "-m", "compileall", "app"], cwd=BACKEND_DIR)
    run_command([str(VENV_PYTHON), "-m", "pytest", "-q"], cwd=BACKEND_DIR)
    if include_frontend_lint:
        run_command([npm_executable(), "run", "lint"], cwd=FRONTEND_DIR)


def run_build() -> None:
    run_command([npm_executable(), "run", "build"], cwd=FRONTEND_DIR)


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen


def stop_processes(processes: list[ManagedProcess]) -> None:
    for item in processes:
        if item.process.poll() is None:
            print(f"[stop] {item.name}")
            item.process.terminate()
    deadline = time.time() + 8
    while time.time() < deadline:
        if all(item.process.poll() is not None for item in processes):
            return
        time.sleep(0.2)
    for item in processes:
        if item.process.poll() is None:
            item.process.kill()


def run_stack(args: argparse.Namespace) -> None:
    if args.setup:
        setup_all()
    elif not VENV_PYTHON.exists():
        raise SystemExit("Backend venv is missing. Run: python build.py setup")

    env = runtime_env()
    processes: list[ManagedProcess] = []
    try:
        api_cmd = [
            str(VENV_PYTHON),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            args.api_host,
            "--port",
            str(args.api_port),
        ]
        if args.reload:
            api_cmd.append("--reload")

        worker_cmd = [str(VENV_PYTHON), "worker.py"]
        frontend_cmd = [
            npm_executable(),
            "run",
            "dev",
            "--",
            "--host",
            args.frontend_host,
            "--port",
            str(args.frontend_port),
        ]

        print("[info] Starting API, worker, and frontend...")
        processes.append(
            ManagedProcess(
                "api",
                subprocess.Popen(api_cmd, cwd=BACKEND_DIR, env=env),
            )
        )
        processes.append(
            ManagedProcess(
                "worker",
                subprocess.Popen(worker_cmd, cwd=BACKEND_DIR, env=env),
            )
        )
        processes.append(
            ManagedProcess(
                "frontend",
                subprocess.Popen(frontend_cmd, cwd=FRONTEND_DIR, env=env),
            )
        )

        print(
            f"[ready] Frontend: http://{args.frontend_host}:{args.frontend_port} | "
            f"API: http://{args.api_host}:{args.api_port}"
        )
        print("[info] Press Ctrl+C to stop all services.")

        while True:
            for item in processes:
                code = item.process.poll()
                if code is not None:
                    raise RuntimeError(f"Process '{item.name}' exited with code {code}")
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[info] Stopping services...")
    finally:
        stop_processes(processes)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NewtonSpect unified build/run script.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("setup", help="Install backend and frontend dependencies.")

    checks_parser = subparsers.add_parser("check", help="Run backend tests and frontend lint.")
    checks_parser.add_argument(
        "--skip-frontend-lint",
        action="store_true",
        help="Skip frontend lint step.",
    )

    build_parser = subparsers.add_parser("build", help="Setup, run checks, and build frontend.")
    build_parser.add_argument(
        "--skip-checks",
        action="store_true",
        help="Skip tests/lint before frontend build.",
    )

    run_parser = subparsers.add_parser("run", help="Run API + worker + frontend dev server.")
    run_parser.add_argument("--no-setup", action="store_true", help="Do not run setup first.")
    run_parser.add_argument("--no-reload", action="store_true", help="Disable uvicorn reload.")
    run_parser.add_argument("--api-host", default="127.0.0.1")
    run_parser.add_argument("--api-port", type=int, default=8000)
    run_parser.add_argument("--frontend-host", default="127.0.0.1")
    run_parser.add_argument("--frontend-port", type=int, default=5173)

    dev_parser = subparsers.add_parser(
        "dev",
        help="Setup, run checks, and start full local stack.",
    )
    dev_parser.add_argument("--skip-checks", action="store_true")
    dev_parser.add_argument("--api-host", default="127.0.0.1")
    dev_parser.add_argument("--api-port", type=int, default=8000)
    dev_parser.add_argument("--frontend-host", default="127.0.0.1")
    dev_parser.add_argument("--frontend-port", type=int, default=5173)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0

    if args.command == "setup":
        setup_all()
        return 0

    if args.command == "check":
        if not VENV_PYTHON.exists():
            setup_all()
        run_checks(include_frontend_lint=not args.skip_frontend_lint)
        return 0

    if args.command == "build":
        setup_all()
        if not args.skip_checks:
            run_checks()
        run_build()
        return 0

    if args.command == "run":
        args.setup = not args.no_setup
        args.reload = not args.no_reload
        run_stack(args)
        return 0

    if args.command == "dev":
        setup_all()
        if not args.skip_checks:
            run_checks()
        args.setup = False
        args.reload = True
        run_stack(args)
        return 0

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

