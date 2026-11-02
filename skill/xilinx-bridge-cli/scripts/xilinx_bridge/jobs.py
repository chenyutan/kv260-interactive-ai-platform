"""后台任务系统：Vivado 综合/实现动辄几十分钟，必须以异步 Job 方式运行。

每个 Job 一个目录: ~/.xilinx-agent-bridge/jobs/<job_id>/
  - cmd.txt       命令行
  - run.tcl       (可选) 生成的 TCL 脚本
  - stdout.log    进程输出
  - status.json   状态与解析结果
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import config
from . import license_schedule
from .file_guard import exclusive
from .process_scope import OwnedProcessTree

IS_WIN = sys.platform.startswith("win")


class ResourceBusyError(RuntimeError):
    def __init__(self, resource: str, owner_job: str = "") -> None:
        self.resource = resource
        self.owner_job = owner_job
        detail = f"，占用 Job: {owner_job}" if owner_job else ""
        super().__init__(f"资源正在被另一个 Job 使用: {resource}{detail}")


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._owned_jobs: set[str] = set()
        self._lock = threading.Lock()
        self._jobs_dir().mkdir(parents=True, exist_ok=True)
        self._load_persisted()

    @staticmethod
    def _jobs_dir() -> Path:
        return config.CONFIG_DIR / "jobs"

    @staticmethod
    def _locks_dir() -> Path:
        return config.CONFIG_DIR / "locks"

    @staticmethod
    def _canonical_resource(resource: str) -> str:
        # Logical locks serialize non-filesystem resources such as one
        # hw_server/JTAG chain.  Path locks keep their previous semantics.
        if resource.startswith("lock:"):
            return resource.casefold() if IS_WIN else resource
        value = str(Path(resource).expanduser().resolve())
        return value.casefold() if IS_WIN else value

    def _resource_lock_path(self, resource: str) -> Path:
        canonical = self._canonical_resource(resource)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        directory = self._locks_dir()
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{digest}.lock"

    def _resource_owner_active(self, payload: dict) -> bool:
        owner_job = payload.get("job_id", "")
        if owner_job:
            status_file = self._jobs_dir() / owner_job / "status.json"
            owner = self._read_status(status_file) if status_file.is_file() else None
            if owner and owner.get("resource_cleanup_incomplete"):
                # A known incomplete teardown is not a stale lease, even if
                # the launcher PID died. Do not race unknown descendants.
                return True
            if owner and (owner.get('scheduler') or (owner.get('meta') or {}).get('owned_process_tree')):
                if ((owner.get('process_tree') or {}).get('cleanup_complete') is not True
                        and not self._pid_exists(owner.get('manager_pid'))):
                    # A manager crash without terminal cleanup evidence cannot
                    # establish that detached descendants released their seats.
                    return True
            if owner and owner.get("status") == "running":
                return (self._pid_exists(owner.get("pid")) or owner_job in self._owned_jobs
                        or self._pid_exists(owner.get("manager_pid")))
            if owner and owner.get("status") in {"success", "failed", "cancelled", "timeout", "interrupted"}:
                # Old managers could publish cancellation before teardown. A
                # terminal label alone is not evidence that the lease is free.
                if owner_job in self._owned_jobs:
                    return True
                if (owner.get('process_tree') or {}).get('cleanup_complete') is True:
                    return False
                return (self._pid_exists(owner.get('pid'))
                        or self._pid_exists(owner.get('manager_pid')))
        return self._pid_exists(payload.get("manager_pid"))

    def _acquire_resource(self, resource: str, job_id: str) -> Path:
        lock_path = self._resource_lock_path(resource)
        try:
            with exclusive(lock_path.with_suffix('.guard')):
                return self._acquire_resource_guarded(resource, job_id)
        except TimeoutError:
            raise ResourceBusyError(resource, "lock-transaction") from None

    def _acquire_resource_guarded(self, resource: str, job_id: str) -> Path:
        canonical = self._canonical_resource(resource)
        lock_path = self._resource_lock_path(canonical)
        payload = {
            "resource": canonical, "job_id": job_id,
            "manager_pid": os.getpid(), "created": time.time(),
        }
        for _ in range(3):
            pending = lock_path.with_name(lock_path.name+"."+uuid.uuid4().hex+".tmp")
            try:
                # Publish a complete inode atomically. An empty O_EXCL target
                # used to be mistaken for an abandoned lock by another manager.
                with pending.open("x", encoding="utf-8") as stream:
                    json.dump(payload, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(pending, lock_path)
                return lock_path
            except FileExistsError:
                try:
                    existing = json.loads(lock_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raise ResourceBusyError(canonical, "unreadable-lock") from None
                if self._resource_owner_active(existing):
                    raise ResourceBusyError(canonical, str(existing.get("job_id", "")))
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise ResourceBusyError(canonical, str(existing.get("job_id", ""))) from exc
            finally:
                pending.unlink(missing_ok=True)
        raise ResourceBusyError(canonical)

    @staticmethod
    def _release_resource(lock_path: Path | None, job_id: str) -> None:
        if lock_path is None:
            return
        try:
            with exclusive(lock_path.with_suffix('.guard')):
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
                if payload.get("job_id") == job_id:
                    lock_path.unlink(missing_ok=True)
        except (OSError, ValueError, TimeoutError):
            pass

    @staticmethod
    def _pid_exists(pid: int | None) -> bool:
        if not pid:
            return False
        try:
            if IS_WIN:
                r = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                return r.returncode == 0 and f'"{pid}"' in r.stdout
            os.kill(pid, 0)
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def _prepare_command(cmd: list[str]) -> list[str] | str:
        """用可跟踪的 cmd.exe 进程执行 .bat/.cmd，避免 shell=True 的短命壳 PID。"""
        if not (IS_WIN and Path(cmd[0]).suffix.lower() in (".bat", ".cmd")):
            return cmd
        comspec = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")

        def quote(arg: str) -> str:
            # /V:OFF 下 ! 不展开；百分号仍会展开，因此成对转义。
            escaped = str(arg).replace("%", "%%").replace('"', '""')
            return f'"{escaped}"'

        # 向 CreateProcess 传单一字符串，避免 Python 对 /C 参数中的引号再转义。
        inner = "call " + " ".join(quote(arg) for arg in cmd)
        return f'{quote(comspec)} /D /V:OFF /S /C "{inner}"'

    def _read_status(self, status_file: Path) -> dict | None:
        try:
            job = json.loads(status_file.read_text(encoding="utf-8"))
            if not isinstance(job, dict) or not job.get("id"):
                return None
            return job
        except (OSError, json.JSONDecodeError):
            return None

    def _load_persisted(self) -> None:
        """扫描磁盘索引，使 server 重启后仍能列出和查询历史 Job。"""
        loaded: dict[str, dict] = {}
        try:
            status_files = self._jobs_dir().glob("*/status.json")
            for status_file in status_files:
                if job := self._read_status(status_file):
                    loaded[job["id"]] = job
        except OSError:
            return
        with self._lock:
            for job_id, job in loaded.items():
                if job_id not in self._owned_jobs:
                    self._jobs[job_id] = job

    # ------------------------------------------------------------------
    @staticmethod
    def alloc_dir(kind: str) -> Path:
        """只分配 job 目录, 不启动进程。用于需要先落盘脚本的场景。"""
        job_id = f"{kind}-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"
        jdir = JobManager._jobs_dir() / job_id
        jdir.mkdir(parents=True, exist_ok=True)
        return jdir

    def submit(self, kind: str, cmd: list[str], cwd: Path, env: dict | None = None,
               meta: dict | None = None, tcl_text: str | None = None,
               jdir: Path | None = None, resource_key: str = "") -> dict:
        budgets = license_schedule.requirements(kind, cmd, meta or {})
        if jdir is None:
            jdir = self.alloc_dir(kind)
        job_id = jdir.name
        jdir.mkdir(parents=True, exist_ok=True)
        resource_lock = self._acquire_resource(resource_key, job_id) if resource_key else None
        try:
            (jdir / "cmd.txt").write_text(
                " ".join(f'"{c}"' for c in cmd), encoding="utf-8"
            )
            if tcl_text is not None:
                (jdir / "run.tcl").write_text(tcl_text, encoding="utf-8")
        except Exception:
            self._release_resource(resource_lock, job_id)
            raise

        job = {
            "id": job_id, "kind": kind, "cmd": cmd, "cwd": str(cwd),
            "dir": str(jdir), "log": str(jdir / "stdout.log"),
            "status": "running", "returncode": None,
            "started": time.time(), "ended": None,
            "summary": None, "meta": meta or {},
            "manager_pid": os.getpid(),
        }
        if resource_key:
            job["resource"] = self._canonical_resource(resource_key)
        with self._lock:
            self._jobs[job_id] = job
            self._owned_jobs.add(job_id)
        self._persist(job)

        def _run():
            capacity_locks: list[Path] = []
            process_scope = None
            terminal_status = None
            full_env = dict(os.environ)
            lic = config.load().get("license_file")
            if lic:
                full_env["XILINXD_LICENSE_FILE"] = lic
                full_env["LM_LICENSE_FILE"] = lic
            if env:
                full_env.update(env)
            try:
                if budgets:
                    job["scheduler"] = {
                        "phase": "waiting", "basis": "operator_configured_capacity",
                        "budgets": dict(budgets), "license_server_queried": False,
                    }
                    self._persist(job)
                    queued_at = time.monotonic()
                    while True:
                        if self._stop_requested(job):
                            return
                        candidates = []
                        for group, capacity in budgets:
                            for slot in range(capacity):
                                try:
                                    lease = self._acquire_resource(
                                        f"lock:license_budget:{group}:{slot}", job_id)
                                    candidates.append(lease)
                                    capacity_locks = list(candidates)
                                    break
                                except ResourceBusyError:
                                    continue
                            else:
                                break
                        if len(candidates) == len(budgets):
                            capacity_locks = candidates
                            break
                        for lease in candidates:
                            self._release_resource(lease, job_id)
                        capacity_locks = []
                        time.sleep(0.1)
                    job["scheduler"].update(
                        phase="admitted", queue_wait_seconds=round(time.monotonic()-queued_at, 6))
                    self._persist(job)
                process_scope = OwnedProcessTree(enabled=bool(budgets or (meta or {}).get("owned_process_tree")))
                with open(job["log"], "w", encoding="utf-8", errors="replace") as lf:
                    popen_cmd = self._prepare_command(cmd)
                    # Keep the pre-spawn cancellation check and PID publication
                    # atomic. Otherwise cancel() can observe pid=None and the
                    # worker can still launch Vivado after being cancelled.
                    with exclusive(jdir/'process.guard'), self._lock:
                        if self._stop_requested(job):
                            return
                        proc = subprocess.Popen(
                            popen_cmd, cwd=str(cwd), env=full_env,
                            stdout=lf, stderr=subprocess.STDOUT,
                            shell=False,
                            start_new_session=not IS_WIN,
                            **({"creationflags": process_scope.creationflags}
                               if IS_WIN and process_scope.creationflags else {}),
                        )
                        job["pid"] = proc.pid
                        self._processes[job_id] = proc
                        process_scope.attach(proc)
                        self._persist(job)
                    proc.wait()
                    with self._lock:
                        job["returncode"] = proc.returncode
                        terminal_status = (
                            job["_terminal_status"] if job.get("_terminal_status")
                            else "success" if proc.returncode == 0
                            else "failed"
                        )
            except Exception as e:  # noqa: BLE001
                with self._lock:
                    terminal_status = "failed"
                    job["summary"] = {"exception": str(e)}
            finally:
                if process_scope is not None:
                    try:
                        cleanup = process_scope.close()
                        if cleanup.get("enabled"):
                            job["process_tree"] = cleanup
                        if cleanup.get("cleanup_complete") is False:
                            job["resource_cleanup_incomplete"] = True
                            terminal_status = "failed"
                            job["summary"] = {**(job.get("summary") or {}),
                                              "exception": "owned_process_tree_cleanup_incomplete"}
                    except Exception as exc:
                        job["resource_cleanup_incomplete"] = True
                        terminal_status = "failed"
                        job["summary"] = {**(job.get("summary") or {}),
                                          "exception": "owned_process_tree_cleanup_failed: "+str(exc)}
                try:
                    with exclusive(jdir/'process.guard', timeout=35), self._lock:
                        self._stop_requested(job)
                        job["status"] = (
                            "failed" if job.get("resource_cleanup_incomplete") else
                            job.get("_terminal_status") or terminal_status or job["status"]
                        )
                        job["ended"] = time.time()
                        self._processes.pop(job_id, None)
                        if not job.get("resource_cleanup_incomplete"):
                            self._release_resource(resource_lock, job_id)
                            for lease in capacity_locks:
                                self._release_resource(lease, job_id)
                        # Publish completion only after teardown/lease release.
                        self._persist(job)
                        self._owned_jobs.discard(job_id)
                except (OSError, TimeoutError) as exc:
                    # Never let an unavailable control guard strand a worker
                    # as "owned" forever or imply successful resource release.
                    with self._lock:
                        job.update(status='failed', ended=time.time(), resource_cleanup_incomplete=True)
                        job['summary'] = {**(job.get('summary') or {}),
                            'exception':'job_finalization_guard_failed: '+str(exc)}
                        self._persist(job)
                        self._processes.pop(job_id, None)
                        self._owned_jobs.discard(job_id)

        worker = threading.Thread(target=_run, daemon=True)
        try:
            worker.start()
        except Exception:
            with self._lock:
                job["status"] = "failed"
                job["ended"] = time.time()
                self._owned_jobs.discard(job_id)
            self._persist(job)
            self._release_resource(resource_lock, job_id)
            raise
        return job

    @contextmanager
    def reserve(self, resource: str, owner: str = "operation"):
        """Reserve a cross-process resource for a bounded synchronous operation.

        KV260 SSH flows need the same fail-fast exclusion as JTAG jobs, but they
        also need local post-processing before returning.  A reservation reuses
        the persistent lock protocol without manufacturing a background Job.
        """
        reservation_id = f"{owner}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        lock_path = self._acquire_resource(resource, reservation_id)
        try:
            yield reservation_id
        finally:
            self._release_resource(lock_path, reservation_id)

    # ------------------------------------------------------------------
    def _persist(self, job: dict) -> None:
        try:
            data = {k: v for k, v in job.items() if k != "cmd" and not k.startswith("_")}
            status_file = Path(job["dir"]) / "status.json"
            tmp_file = status_file.with_suffix(".json.tmp")
            tmp_file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(tmp_file, status_file)
        except (OSError, TypeError, ValueError):
            pass

    def set_summary(self, job_id: str, summary: dict) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["summary"] = summary
                self._persist(self._jobs[job_id])

    def get(self, job_id: str) -> dict | None:
        if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,180}', job_id):
            return None
        if (self._jobs_dir()/job_id).resolve().parent != self._jobs_dir().resolve():
            return None
        with self._lock:
            job = self._jobs.get(job_id)
            is_active = job_id in self._owned_jobs
        if not is_active:
            sf = self._jobs_dir() / job_id / "status.json"
            disk_job = self._read_status(sf) if sf.exists() else None
            if disk_job is not None:
                job = disk_job
                with self._lock:
                    self._jobs[job_id] = job
        if job and job.get("status") == "running" and not is_active:
            if not self._pid_exists(job.get("pid")) and not self._pid_exists(job.get("manager_pid")):
                completion = (job.get("meta") or {}).get("completion_result", "")
                recovered = None
                if completion:
                    try:
                        recovered = json.loads(Path(completion).read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        recovered = None
                if isinstance(recovered, dict) and recovered.get("status") in {"success", "error"}:
                    job["status"] = "success" if recovered["status"] == "success" else "failed"
                    job["returncode"] = 0 if recovered["status"] == "success" else 2
                    job["ended"] = job.get("ended") or time.time()
                else:
                    job["status"] = "interrupted"
                    job["ended"] = job.get("ended") or time.time()
                    job["summary"] = job.get("summary") or {
                        "exception": "MCP 服务重启或异常退出，且原 Job 进程已不存在"
                    }
                self._persist(job)
        return job

    @staticmethod
    def _stop_requested(job: dict) -> bool:
        request = Path(job["dir"]) / "termination.json"
        if request.is_file():
            try:
                status = json.loads(request.read_text(encoding="utf-8")).get("status")
                if status in {"cancelled", "timeout", "failed"}:
                    job["_cancel_requested"] = True
                    job["_terminal_status"] = status
            except (OSError, ValueError, AttributeError):
                # Unreadable cancellation must not accidentally launch a child.
                job["_cancel_requested"] = True
                job["_terminal_status"] = "failed"
        return bool(job.get("_cancel_requested"))

    def list(self, limit: int = 20) -> list[dict]:
        self._load_persisted()
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.get("started", 0), reverse=True)
        return [
            {k: j.get(k) for k in ("id", "kind", "status", "returncode", "started", "ended")}
            for j in jobs[:limit]
        ]

    def _terminate(self, job_id: str, final_status: str) -> bool:
        if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,180}', job_id):
            return False
        directory = self._jobs_dir()/job_id
        if not directory.is_dir() or directory.resolve().parent != self._jobs_dir().resolve():
            return False
        try:
            with exclusive(directory/'process.guard'):
                accepted, pid = self._request_termination_guarded(job_id, final_status)
        except TimeoutError:
            return False
        if not accepted:
            return False
        # taskkill may take 30s. It must not hold the short launch/finalization
        # guard: the worker is responsible for cleanup and the terminal state.
        killed = True
        if pid:
            try:
                if IS_WIN:
                    result = subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        capture_output=True, timeout=30,
                    )
                    killed = result.returncode == 0 or not self._pid_exists(pid)
                else:
                    os.killpg(pid, 15)
            except (OSError, subprocess.SubprocessError):
                killed = not self._pid_exists(pid)
        return killed

    def _request_termination_guarded(self, job_id: str, final_status: str) -> tuple[bool, int | None]:
        job = self.get(job_id)
        if not job or job["status"] != "running":
            return False, None
        request = Path(job["dir"]) / "termination.json"
        pending = request.with_name(request.name+"."+uuid.uuid4().hex+".tmp")
        try:
            pending.write_text(json.dumps({"status": final_status}), encoding="utf-8")
            os.replace(pending, request)
        finally:
            pending.unlink(missing_ok=True)
        with self._lock:
            job["_cancel_requested"] = True
            job["_terminal_status"] = final_status
            job["termination_requested"] = final_status
            self._persist(job)
            return True, job.get('pid')

    def cancel(self, job_id: str) -> bool:
        return self._terminate(job_id, "cancelled")

    def timeout(self, job_id: str) -> bool:
        """请求超时终止；仅在 worker 完成清理后发布终态。"""
        return self._terminate(job_id, "timeout")

    def fail(self, job_id: str) -> bool:
        """宿主基础设施已明确失败时，提前终止仍在等待的父进程。"""
        return self._terminate(job_id, "failed")


MANAGER = JobManager()


def tail(path: str, n: int = 60) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except FileNotFoundError:
        return "(日志尚未生成)"
