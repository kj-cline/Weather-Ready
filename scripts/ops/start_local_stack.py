from __future__ import annotations

import argparse
import contextlib
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from stormready_v3.config.settings import DEFAULT_DB_PATH
from stormready_v3.config.runtime import runtime_configuration_dict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
UVICORN_APP = "stormready_v3.api.app:app"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start local StormReady V3 processes.")
    parser.add_argument("--ui", action="store_true", help="Run the React frontend and local API.")
    parser.add_argument("--supervisor-once", action="store_true", help="Run one supervisor tick before exiting.")
    parser.add_argument("--supervisor-loop", action="store_true", help="Run the supervisor loop in the foreground.")
    parser.add_argument("--interval-seconds", type=int, default=300, help="Supervisor loop interval in seconds.")
    parser.add_argument("--show-config", action="store_true", help="Print runtime configuration before starting.")
    parser.add_argument("--init-db-if-missing", action="store_true", help="Initialize the local DB if it does not exist.")
    parser.add_argument("--open-browser", action="store_true", help="Open the frontend in a browser after startup.")
    parser.add_argument(
        "--no-supervisor",
        action="store_true",
        help="Do not start the background supervisor automatically when launching the UI.",
    )
    parser.add_argument("--api-port", type=int, default=8000, help="Port to use for the FastAPI backend.")
    parser.add_argument("--frontend-port", type=int, default=5173, help="Port to use for the React frontend.")
    return parser.parse_args()


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    src_path = str(PROJECT_ROOT / "src")
    env["PYTHONPATH"] = src_path if not existing else f"{src_path}:{existing}"
    env.setdefault("STORMREADY_V3_SOURCE_MODE", "live")
    return env


def _python_executable() -> str:
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _ensure_db_initialized(*, python: str, env: dict[str, str]) -> None:
    if DEFAULT_DB_PATH.exists():
        return
    DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [python, str(PROJECT_ROOT / "scripts" / "ops" / "init_db.py")],
        check=True,
        cwd=str(PROJECT_ROOT),
        env=env,
    )


def _wait_for_port(host: str, port: int, timeout_seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _browser_opener(url: str, *, port: int) -> None:
    if _wait_for_port("127.0.0.1", port):
        webbrowser.open(url, new=2)


def main() -> None:
    args = parse_args()
    if args.show_config:
        print(runtime_configuration_dict())

    python = _python_executable()
    env = _base_env()
    processes: list[subprocess.Popen[str]] = []
    background_supervisor_for_ui = args.ui and not args.no_supervisor

    if args.init_db_if_missing:
        _ensure_db_initialized(python=python, env=env)

    if args.supervisor_once:
        subprocess.run(
            [python, str(PROJECT_ROOT / "scripts" / "ops" / "run_supervisor_loop.py"), "--once"],
            check=True,
            cwd=str(PROJECT_ROOT),
            env=env,
        )

    if args.supervisor_loop and not args.ui:
        processes.append(
            subprocess.Popen(
                [
                    python,
                    str(PROJECT_ROOT / "scripts" / "ops" / "run_supervisor_loop.py"),
                    "--interval-seconds",
                    str(args.interval_seconds),
                ],
                cwd=str(PROJECT_ROOT),
                env=env,
            )
        )

    if args.ui:
        ui_env = env.copy()
        if background_supervisor_for_ui:
            ui_env["STORMREADY_V3_BACKGROUND_SUPERVISOR"] = "1"
            ui_env["STORMREADY_V3_BACKGROUND_SUPERVISOR_INTERVAL_SECONDS"] = str(args.interval_seconds)
        frontend_url = f"http://127.0.0.1:{args.frontend_port}"
        if args.open_browser:
            threading.Thread(
                target=_browser_opener,
                args=(frontend_url,),
                kwargs={"port": args.frontend_port},
                daemon=True,
            ).start()
        processes.append(
            subprocess.Popen(
                [
                    python,
                    "-m",
                    "uvicorn",
                    UVICORN_APP,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.api_port),
                ],
                cwd=str(PROJECT_ROOT),
                env=ui_env,
            )
        )
        processes.append(
            subprocess.Popen(
                [
                    "npm",
                    "run",
                    "dev",
                    "--",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.frontend_port),
                ],
                cwd=str(PROJECT_ROOT / "frontend"),
                env=ui_env,
            )
        )

    if not processes:
        print("Nothing started. Use --ui, --supervisor-once, or --supervisor-loop.")
        return

    try:
        while any(process.poll() is None for process in processes):
            time.sleep(1)
    except KeyboardInterrupt:
        for process in processes:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
        for process in processes:
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
