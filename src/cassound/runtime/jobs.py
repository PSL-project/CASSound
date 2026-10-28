"""Bounded scheduling, dedicated child processes and JSON-only control messages."""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time

from ..domain.errors import CASSoundError, Cancelled
from ..domain.types import Event, freeze

log = logging.getLogger(__name__)


def terminate_tree(process):
    if os.name == "nt":
        if process.poll() is not None:
            return
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class Job:
    def __init__(self, command):
        self.command = command
        self.request_id = command.request_id
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._state = "queued"
        self._future = None
        self.metrics = {}
        self.sequence = 0
        self._committing = False
        self._process = None

    @property
    def state(self):
        with self._lock:
            return self._state

    def cancel(self):
        with self._lock:
            if self._committing or self._state in {"succeeded", "failed", "cancelled"}:
                return False
            self._cancel.set()
            return True

    def wait(self, timeout=None):
        return self._future.result(timeout)


class JobManager:
    def __init__(self, workdir, emit, max_workers=2, timeout=1800):
        if not 1 <= max_workers <= 4:
            raise ValueError("Worker count must be 1–4.")
        self.workdir = Path(workdir)
        self.emit = emit
        self.timeout = timeout
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cassound-job")
        self.jobs = []
        self.closed = False
        self._lock = threading.RLock()

    def event(self, job, name, payload=None):
        job.sequence += 1
        self.emit(Event(job.request_id, job.command.project_id, name, job.sequence, freeze(payload or {})))

    def submit(self, command, commit=lambda x: x, limits=None, private=None):
        with self._lock:
            if self.closed:
                raise CASSoundError("Session is closed.")
            # A bounded queue also prevents an unlimited build-up of captured payloads.
            if sum(j.state in {"queued", "running"} for j in self.jobs) >= 32:
                raise CASSoundError("Job queue is full.", code="resource_limit")
            job = Job(command)
            job._private = private
            self.jobs.append(job)
            self.event(job, "job.queued")
            job._future = self.pool.submit(self._run, job, commit, limits or {})
            return job

    def _run(self, job, commit, limits):
        process = None
        stage = self.workdir / "staging" / job.request_id
        try:
            if job._cancel.is_set():
                raise Cancelled("Job cancelled before execution.")
            stage.mkdir(parents=True)
            with job._lock:
                job._state = "running"
            self.event(job, "job.started")
            args = (
                [sys.executable, "--worker"]
                if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "cassound.runtime.worker"]
            )
            if getattr(sys, "frozen", False):
                sibling = Path(sys.executable).with_name(
                    "CASSoundCLI.exe" if os.name == "nt" else "CASSoundCLI"
                )
                if sibling.is_file():
                    args = [str(sibling), "--worker"]
            options = (
                {"start_new_session": True}
                if os.name != "nt"
                else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
            )
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            # Bound BLAS threads in each worker; no GUI initialization in children.
            env["SSQ_PARALLEL"] = "0"
            env["NUMBA_NUM_THREADS"] = "1"
            for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
                env[key] = "1"
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
                **options,
            )
            request = {
                "command": job.command.to_dict(),
                "workdir": str(self.workdir),
                "stage": str(stage),
                "limits": limits,
            }
            job._process = process
            secret = job._private or ""
            request["private_size"] = len(secret)
            process.stdin.write(json.dumps(request, ensure_ascii=False, allow_nan=False) + "\n" + secret)
            job._private = None
            process.stdin.close()
            messages = queue.Queue(maxsize=1024)

            def reader():
                try:
                    for line in process.stdout:
                        if len(line) > 4 * 1024**2:
                            messages.put(
                                {"error": "Worker message exceeded its bound.", "code": "resource_limit"}
                            )
                            break
                        messages.put(json.loads(line))
                except (ValueError, OSError) as exc:
                    messages.put({"error": str(exc), "code": "worker_protocol"})
                finally:
                    messages.put(None)

            thread = threading.Thread(target=reader, daemon=True)
            thread.start()

            # Worker stderr is bounded in memory, drained to avoid pipe deadlock.
            def drain():
                while process.stderr.read(4096):
                    pass

            threading.Thread(target=drain, daemon=True).start()
            start = time.monotonic()
            result = None
            completed = False
            while True:
                if job._cancel.is_set():
                    raise Cancelled("Job cancelled.")
                if time.monotonic() - start > self.timeout:
                    raise CASSoundError("Worker timed out.", code="timeout")
                try:
                    msg = messages.get(timeout=0.05)
                except queue.Empty:
                    continue
                if msg is None:
                    break
                if "error" in msg:
                    raise CASSoundError(msg["error"], code=msg.get("code", "processing_failed"))
                if "result" in msg:
                    result = msg["result"]
                    job.metrics = msg.get("metrics", {})
                    completed = True
                elif "progress" in msg:
                    self.event(job, "job.progress", msg["progress"])
            if process.wait(timeout=5) != 0 or not completed:
                raise CASSoundError("Worker exited without a complete result.", code="worker_crashed")
            # Cancellation and commit are serialized: a successful cancel never commits a result.
            with job._lock:
                if job._cancel.is_set():
                    raise Cancelled("Job cancelled.")
                job._committing = True
            result = commit(result)
            with job._lock:
                job._state = "succeeded"
            self.event(job, "job.succeeded")
            return result
        except Cancelled:
            with job._lock:
                job._state = "cancelled"
            self.event(job, "job.cancelled")
            raise
        except Exception as exc:
            with job._lock:
                job._state = "failed"
            self.event(
                job, "job.failed", {"code": getattr(exc, "code", "processing_failed"), "message": str(exc)}
            )
            raise
        finally:
            job._private = None
            if process:
                terminate_tree(process)
                process.wait()
            import shutil

            shutil.rmtree(stage, ignore_errors=True)
            for field in ("snapshot_token", "review_token", "recipe_token"):
                token = job.command.payload.get(field)
                if (
                    isinstance(token, str)
                    and len(token) == 32
                    and all(c in "0123456789abcdef" for c in token)
                ):
                    (self.workdir / "requests" / (token + ".json")).unlink(missing_ok=True)

    def close(self):
        with self._lock:
            self.closed = True
        for job in self.jobs:
            job.cancel()
        self.pool.shutdown(wait=True, cancel_futures=False)
