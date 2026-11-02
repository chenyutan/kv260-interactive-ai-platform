"""Token-bounded Vitis AI access through native WSL Conda environments.

One fused entry point deliberately covers discovery, user quantization scripts,
XMODEL compilation, and a real NNDCT smoke test.  Commands are argv arrays (no
user-controlled shell), complete logs stay on disk, and successful responses
contain only the facts an agent normally needs for its next decision.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from . import config


class VitisAIError(ValueError):
    pass


_ACTIONS = {"inspect", "analyze", "quantize", "compile", "pipeline", "smoke"}
_DETAILS = {"compact", "standard"}
_VERSIONS = {"auto", "3.0", "3.5"}
_DISTRO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_NETWORK = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,126}\Z")
_ENVIRONMENTS = {"3.5": "vitis-ai-pytorch", "3.0": "vitis-ai-3.0-pytorch"}
_PAIRING = {
    "3.5": "Vitis AI 3.5 / AMD 2023.1 release family",
    "3.0": "Vitis AI 3.0 / AMD 2022.2 release family",
}
_INSPECT_CACHE: dict[tuple[str, str, str], tuple[float, dict]] = {}
_MAX_ARGS = 64
_MAX_TAIL_LINES = 18
_MAX_CACHE_FILES = 10_000
_MAX_CACHE_BYTES = 2 * 1024 * 1024 * 1024

_PYTORCH_30_CPU_SHIM = (
    "import runpy,sys,torch;"
    "torch.__version__=torch.__version__.split('+',1)[0];"
    "p=sys.argv.pop(1);sys.argv[0]=p;runpy.run_path(p,run_name='__main__')"
)

_XIR_HELPER = r'''
import collections
import hashlib
import pathlib
import xir

def _tensor(tensor):
    return {"name": tensor.name[-128:], "dims": list(tensor.dims), "dtype": str(tensor.dtype)}

def _xir_summary(model_path):
    path = pathlib.Path(model_path)
    graph = xir.Graph.deserialize(str(path))
    root = graph.get_root_subgraph()
    children = list(root.toposort_child_subgraph())
    devices = collections.Counter()
    compact = []
    dpu_ops = 0
    fingerprints = []
    for child in children[:32]:
        device = str(child.get_attr("device")) if child.has_attr("device") else "UNKNOWN"
        devices[device] += 1
        ops = int(child.get_op_num())
        if device == "DPU":
            dpu_ops += ops
        item = {"device": device, "ops": ops}
        if child.has_attr("dpu_name"):
            item["dpu_name"] = str(child.get_attr("dpu_name"))
        if child.has_attr("dpu_fingerprint"):
            value = str(child.get_attr("dpu_fingerprint"))
            item["dpu_fingerprint"] = value
            fingerprints.append(value)
        inputs = [_tensor(t) for t in list(child.get_input_tensors())[:8]]
        outputs = [_tensor(t) for t in list(child.get_output_tensors())[:8]]
        if inputs:
            item["inputs"] = inputs
        if outputs:
            item["outputs"] = outputs
        compact.append(item)
    op_types = collections.Counter(op.get_type() for op in graph.get_ops())
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {
        "graph": graph.get_name(), "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(), "ops": len(graph.get_ops()),
        "dpu_ops": dpu_ops, "subgraphs": len(children),
        "devices": dict(sorted(devices.items())),
        "dpu_fingerprints": sorted(set(fingerprints)),
        "outputs": [_tensor(t) for t in list(root.get_output_tensors())[:8]],
        "partitions": compact,
        "op_types": dict(sorted(op_types.items())),
    }
'''

_ANALYZE_SCRIPT = _XIR_HELPER + r'''
import json
import sys
print("XAB_VAI_MODEL=" + json.dumps(_xir_summary(sys.argv[1]), separators=(",", ":")))
'''

_COMPILE_DRIVER = _XIR_HELPER + r'''
import json
import subprocess
import sys
compiler, model, arch, output, network, *options = sys.argv[1:]
command = [compiler, "-x", model, "-a", arch, "-o", output, "-n", network, *options]
completed = subprocess.run(command, shell=False)
if completed.returncode:
    raise SystemExit(completed.returncode)
compiled = pathlib.Path(output) / (network + ".xmodel")
if not compiled.is_file():
    candidates = sorted(pathlib.Path(output).glob("*.xmodel"))
    if len(candidates) != 1:
        raise RuntimeError("compiler did not produce one unambiguous XMODEL")
    compiled = candidates[0]
print("XAB_VAI_MODEL=" + json.dumps(_xir_summary(compiled), separators=(",", ":")))
'''


_SMOKE_SCRIPT = r'''from pathlib import Path
import json
import os
import torch
from pytorch_nndct.apis import torch_quantizer

torch.manual_seed(7)

class TinyVision(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.conv = torch.nn.Conv2d(3, 4, 3, padding=1)
        self.relu = torch.nn.ReLU()
    def forward(self, image):
        return self.relu(self.conv(image))

out = Path(os.environ["XAB_VAI_OUTPUT_DIR"])
out.mkdir(parents=True, exist_ok=True)
sample = torch.linspace(-1.0, 1.0, 3 * 16 * 16).reshape(1, 3, 16, 16)
quantizer = torch_quantizer("calib", TinyVision().eval(), (sample,), output_dir=str(out))
with torch.no_grad():
    result = quantizer.quant_model(sample)
quantizer.export_quant_config()
quant_info = out / "quant_info.json"
if not quant_info.is_file() or quant_info.stat().st_size == 0:
    raise RuntimeError("NNDCT did not create quant_info.json")
test_quantizer = torch_quantizer("test", TinyVision().eval(), (sample,), output_dir=str(out))
with torch.no_grad():
    test_result = test_quantizer.quant_model(sample)
test_quantizer.export_xmodel(output_dir=str(out), deploy_check=False)
xmodels = list(out.glob("*.xmodel"))
if not xmodels:
    raise RuntimeError("NNDCT did not export an XMODEL")
summary = {"ok": True, "shape": list(test_result.shape), "quant_info_bytes": quant_info.stat().st_size,
           "xmodel": xmodels[0].name, "xmodel_bytes": xmodels[0].stat().st_size}
print("XAB_VAI_RESULT=" + json.dumps(summary, separators=(",", ":")))
'''


def _tail(path: Path, lines: int = _MAX_TAIL_LINES) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    value = "\n".join(content[-lines:]).replace("\x00", "")
    value = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", value)
    return value[-4000:]


def _validate_text_list(values: list[str] | None, label: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > _MAX_ARGS:
        raise VitisAIError(f"{label} 必须是最多 {_MAX_ARGS} 项的字符串数组")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or "\x00" in value or len(value) > 4096:
            raise VitisAIError(f"{label} 每项必须是长度不超过4096的字符串")
        result.append(value)
    return result


def _resolve_version(version: str) -> str:
    if version not in _VERSIONS:
        raise VitisAIError("version 必须是 auto、3.5 或 3.0")
    if version != "auto":
        return version
    selected = str(config.load().get("selected_version", "")).strip()
    return "3.0" if selected == "2022.2" else "3.5"


def _wsl_executable() -> str:
    executable = shutil.which("wsl.exe") or shutil.which("wsl")
    if not executable:
        raise VitisAIError("未找到 WSL；请先安装 WSL2 Ubuntu")
    return executable


def _capture(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VitisAIError(f"WSL 命令超时（{timeout}s）") from exc
    except OSError as exc:
        raise VitisAIError(f"无法启动 WSL: {exc}") from exc


def _discover_conda(wsl: str, distro: str) -> str:
    override = os.environ.get("XAB_VITIS_AI_CONDA", "").strip()
    if override:
        if not override.startswith("/") or any(ch in override for ch in "\r\n\x00"):
            raise VitisAIError("XAB_VITIS_AI_CONDA 必须是 WSL 内绝对路径")
        probe = _capture([wsl, "-d", distro, "--", "/usr/bin/test", "-x", override], 15)
        if probe.returncode != 0:
            raise VitisAIError(f"配置的 Conda 不可执行: {override}")
        return override
    # Static script only: no user value is interpolated into the shell.
    probe = _capture([
        wsl, "-d", distro, "--", "/bin/sh", "-lc",
        'if [ -x "$HOME/amd/miniconda3/bin/conda" ]; then printf "%s" "$HOME/amd/miniconda3/bin/conda"; '
        'elif command -v conda >/dev/null 2>&1; then command -v conda; fi',
    ], 15)
    conda = probe.stdout.strip().replace("\x00", "")
    if probe.returncode != 0 or not conda.startswith("/"):
        raise VitisAIError("WSL 中未找到 Vitis AI Conda；可用 XAB_VITIS_AI_CONDA 指定")
    return conda


def _wsl_path(wsl: str, distro: str, value: str, *, must_exist: bool) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise VitisAIError("路径不能为空")
    value = value.strip()
    if value.startswith("/"):
        if must_exist:
            probe = _capture([wsl, "-d", distro, "--", "/usr/bin/test", "-e", value], 15)
            if probe.returncode != 0:
                raise VitisAIError(f"WSL 路径不存在: {value}")
        return value
    host = Path(value).expanduser()
    if must_exist and not host.exists():
        raise VitisAIError(f"路径不存在: {value}")
    if not must_exist:
        host.mkdir(parents=True, exist_ok=True)
    # wsl.exe consumes backslashes in argv before wslpath sees them.  Forward
    # slashes preserve Windows drive paths and also work with spaces.
    resolved = str(host.resolve()).replace("\\", "/")
    converted = _capture([wsl, "-d", distro, "--", "wslpath", "-a", resolved], 15)
    path = converted.stdout.strip().replace("\x00", "")
    if converted.returncode != 0 or not path.startswith("/"):
        raise VitisAIError(f"无法把 Windows 路径转换到 WSL: {value}")
    return path


def _host_artifact_path(wsl_path: str) -> str:
    """Keep WSL-only paths truthful; mounted Windows paths are already returned elsewhere."""
    return wsl_path


def _run_logged(args: list[str], timeout: int, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as stream:
            completed = subprocess.run(
                args, stdout=stream, stderr=subprocess.STDOUT, text=True,
                timeout=timeout, shell=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise VitisAIError(
            f"Vitis AI 命令超时（{timeout}s）；日志: {log_path}"
        ) from exc
    except OSError as exc:
        raise VitisAIError(f"无法启动 Vitis AI: {exc}") from exc
    return completed.returncode


def _run_dir(action: str) -> Path:
    path = config.CONFIG_DIR / "vitis_ai" / "runs" / (
        f"{time.strftime('%Y%m%d-%H%M%S')}-{action}-{uuid.uuid4().hex[:8]}"
    )
    path.mkdir(parents=True, exist_ok=False)
    return path


def _python_script_argv(version: str, script: str, args: list[str]) -> list[str]:
    """Work around VAI 3.0 CPU's false `1.12.1+cpu` compatibility failure."""
    if version == "3.0":
        return ["python", "-c", _PYTORCH_30_CPU_SHIM, script, *args]
    return ["python", script, *args]


def _inspect_environment(version: str, distro: str, refresh: bool) -> dict:
    wsl = _wsl_executable()
    conda = _discover_conda(wsl, distro)
    key = (distro, conda, version)
    cached = _INSPECT_CACHE.get(key)
    if not refresh and cached and time.monotonic() - cached[0] < 60:
        result = dict(cached[1])
        result["cache"] = {"hit": True, "ttl_seconds": 60}
        return result
    env_name = _ENVIRONMENTS[version]
    root = str(Path(conda).parent.parent).replace("\\", "/")
    env_root = f"{root}/envs/{env_name}"
    compiler = f"{env_root}/bin/vai_c_xir"
    arch = f"{env_root}/lib/python{'3.8' if version == '3.5' else '3.7'}/site-packages/vaic/arch/DPUCZDX8G/KV260/arch.json"
    code = (
        "import importlib.util,json,pathlib,shutil,torch;"
        "mods={n:bool(importlib.util.find_spec(n)) for n in ['pytorch_nndct','xir','vart']};"
        "v=importlib.util.find_spec('vaic');"
        "b=next(iter(v.submodule_search_locations),'') if v and v.submodule_search_locations else '';"
        "a=str(pathlib.Path(b)/'arch/DPUCZDX8G/KV260/arch.json') if b else '';"
        "print('XAB_VAI_RESULT='+json.dumps({'torch':torch.__version__,'modules':mods,'arch':a,'compiler':shutil.which('vai_c_xir') or ''},separators=(',',':')))"
    )
    probe = _capture([
        wsl, "-d", distro, "--", conda, "run", "-n", env_name,
        "--no-capture-output", "python", "-c", code,
    ], 45)
    payload: dict[str, Any] = {}
    for line in probe.stdout.splitlines():
        if line.startswith("XAB_VAI_RESULT="):
            try:
                payload = json.loads(line.partition("=")[2])
            except json.JSONDecodeError:
                payload = {}
    if str(payload.get("arch", "")).startswith("/"):
        arch = payload["arch"]
    if str(payload.get("compiler", "")).startswith("/"):
        compiler = payload["compiler"]
    checks = {}
    for label, candidate in (("compiler", compiler), ("kv260_arch", arch)):
        flag = "-x" if label == "compiler" else "-f"
        test = _capture([wsl, "-d", distro, "--", "/usr/bin/test", flag, candidate], 15)
        checks[label] = test.returncode == 0
    modules = payload.get("modules", {})
    xir_available = bool(modules.get("xir"))
    vart_available = bool(modules.get("vart"))
    capabilities = {
        "quantize": probe.returncode == 0 and bool(modules.get("pytorch_nndct")),
        "compile": checks["compiler"] and checks["kv260_arch"],
        "compiler": checks["compiler"],
        "analyze": xir_available,
        "runtime_api": xir_available and vart_available,
        "kv260_arch": checks["kv260_arch"],
    }
    result = {
        "status": "ready" if all(capabilities.values()) else "incomplete",
        "action": "inspect", "version": version, "environment": env_name,
        "pairing": _PAIRING[version], "capabilities": capabilities,
        "torch": payload.get("torch", "unknown"),
        "paths": {"conda": conda, "compiler": compiler, "kv260_arch": arch},
        "cache": {"hit": False, "ttl_seconds": 60},
        "limitations": ["KV260_BOARD_INFERENCE_NOT_VERIFIED"],
    }
    _INSPECT_CACHE[key] = (time.monotonic(), dict(result))
    return result


def _project_inspect(result: dict, detail: str) -> dict:
    if detail == "standard":
        return result
    return {key: result[key] for key in (
        "status", "action", "version", "environment", "capabilities",
        "cache", "limitations",
    )}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _wsl_artifacts(wsl: str, distro: str, directory: str) -> list[str]:
    found = _capture([
        wsl, "-d", distro, "--", "/usr/bin/find", directory,
        "-maxdepth", "2", "-type", "f",
    ], 30)
    if found.returncode != 0:
        return []
    return sorted(
        line.replace("\x00", "").strip() for line in found.stdout.splitlines()
        if line.replace("\x00", "").strip().startswith("/")
    )[:32]


def _fingerprint_model(model: str, version: str, network: str,
                       options: list[str]) -> str:
    path = Path(model).expanduser()
    if path.is_file():
        identity: Any = {"sha256": _sha256(path.resolve())}
    else:
        identity = {"wsl_path": model}
    body = {"model": identity, "version": version, "network": network, "options": options}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _fingerprint_quantize(script: str, args: list[str], version: str,
                          cache_inputs: list[str]) -> str | None:
    """Hash explicitly declared host inputs; omission deliberately disables cache."""
    if not cache_inputs:
        return None
    script_path = Path(script).expanduser()
    if not script_path.is_file() or script_path.is_symlink():
        raise VitisAIError("启用量化缓存时 script 必须是非符号链接的 Windows 文件")
    entries: list[dict] = []
    total_files = 0
    total_bytes = 0
    for raw in cache_inputs:
        source = Path(raw).expanduser()
        if not source.exists() or source.is_symlink():
            raise VitisAIError(f"cache_inputs 不存在或是符号链接: {raw}")
        files = [source] if source.is_file() else sorted(
            path for path in source.rglob("*") if path.is_file()
        )
        item_files = []
        for path in files:
            if path.is_symlink():
                raise VitisAIError(f"cache_inputs 内不允许符号链接文件: {path}")
            total_files += 1
            size = path.stat().st_size
            total_bytes += size
            if total_files > _MAX_CACHE_FILES or total_bytes > _MAX_CACHE_BYTES:
                raise VitisAIError(
                    "量化缓存指纹超过10000文件或2GiB；缩小cache_inputs，或省略以禁用缓存"
                )
            label = path.name if source.is_file() else path.relative_to(source).as_posix()
            item_files.append({"path": label, "bytes": size, "sha256": _sha256(path)})
        entries.append({"root": source.name, "files": item_files})
    body = {
        "script_sha256": _sha256(script_path), "args": args,
        "version": version, "inputs": entries,
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _parse_smoke_result(log_path: Path) -> dict:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    for raw_line in reversed(lines):
        line = raw_line.replace("\x00", "").strip()
        if line.startswith("XAB_VAI_RESULT="):
            try:
                return json.loads(line.partition("=")[2])
            except json.JSONDecodeError:
                return {}
    return {}


def _parse_model_result(log_path: Path) -> dict:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    for raw_line in reversed(lines):
        line = raw_line.replace("\x00", "").strip()
        if line.startswith("XAB_VAI_MODEL="):
            try:
                return json.loads(line.partition("=")[2])
            except json.JSONDecodeError:
                return {}
    return {}


def _project_model(summary: dict, detail: str) -> dict:
    if detail == "standard":
        return summary
    return {
        key: summary[key] for key in (
            "graph", "bytes", "sha256", "ops", "dpu_ops", "subgraphs",
            "devices", "dpu_fingerprints", "outputs",
        ) if key in summary
    }


def validate_request(action: str = "inspect", work_dir: str = "", model: str = "",
                     script: str = "", args: list[str] | None = None,
                     version: str = "auto", network_name: str = "xab_model",
                     compiler_options: list[str] | None = None, timeout: int = 600,
                     force: bool = False, detail: str = "compact",
                     distro: str = "Ubuntu", refresh: bool = False,
                     cache_inputs: list[str] | None = None, arch_json: str = "") -> dict:
    """Validate without starting WSL so background Jobs can fail fast."""
    for label, value in (("action", action), ("work_dir", work_dir), ("model", model),
                         ("script", script), ("version", version),
                         ("network_name", network_name), ("detail", detail),
                         ("distro", distro), ("arch_json", arch_json)):
        if not isinstance(value, str) or "\x00" in value:
            raise VitisAIError(f"{label} 必须是无NUL字符的字符串")
    if action not in _ACTIONS:
        raise VitisAIError(f"action 必须是 {sorted(_ACTIONS)}")
    if arch_json:
        if action not in {"compile", "pipeline"}:
            raise VitisAIError("arch_json 仅用于 compile/pipeline")
        arch_path = Path(arch_json).expanduser()
        if not arch_path.is_file() or not 1 <= arch_path.stat().st_size <= 65536:
            raise VitisAIError("arch_json 必须是本机可读的 1..65536 字节 JSON 文件")
        try:
            architecture = json.loads(arch_path.read_text(encoding="utf-8"))
            if not isinstance(architecture, dict) or not any(
                isinstance(architecture.get(key), str) and architecture[key].strip()
                for key in ("target", "fingerprint")
            ):
                raise ValueError("missing target/fingerprint")
        except (ValueError, OSError) as exc:
            raise VitisAIError(f"arch_json 无效: {exc}") from exc
    if detail not in _DETAILS:
        raise VitisAIError("detail 必须是 compact 或 standard")
    if not _DISTRO.fullmatch(distro):
        raise VitisAIError("distro 不是安全的 WSL 发行版名称")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 10 <= timeout <= 7200:
        raise VitisAIError("timeout 必须是10..7200秒的整数")
    if not isinstance(force, bool) or not isinstance(refresh, bool):
        raise VitisAIError("force/refresh 必须是布尔值")
    user_args = _validate_text_list(args, "args")
    options = _validate_text_list(compiler_options, "compiler_options")
    declared_inputs = _validate_text_list(cache_inputs, "cache_inputs")
    if declared_inputs and action not in {"quantize", "pipeline"}:
        raise VitisAIError("cache_inputs 仅用于 quantize 或 pipeline")
    if action in {"analyze", "compile"} and not model:
        raise VitisAIError(f"{action} 需要 model=.xmodel 路径")
    if action in {"compile", "pipeline"}:
        if not _NETWORK.fullmatch(network_name):
            raise VitisAIError("network_name 必须是安全的标识符")
        managed = ("--xmodel", "--arch", "--output_dir", "--net_name")
        for option in options:
            flag = option.split("=", 1)[0]
            if option.startswith(("-x", "-a", "-o", "-n")) or (
                flag.startswith("--") and any(name.startswith(flag) for name in managed)
            ):
                raise VitisAIError("compiler_options 不能覆盖 model/arch/output/network 参数")
    if action in {"quantize", "pipeline"} and not script:
        raise VitisAIError(f"{action} 需要 script=量化 Python 脚本")
    return {
        "action": action, "work_dir": work_dir, "model": model, "script": script,
        "args": user_args, "version": _resolve_version(version),
        "network_name": network_name, "compiler_options": options,
        "timeout": timeout, "force": force, "detail": detail,
        "distro": distro, "refresh": refresh, "cache_inputs": declared_inputs,
        "arch_json": arch_json,
    }


def flow(action: str = "inspect", work_dir: str = "", model: str = "",
         script: str = "", args: list[str] | None = None,
         version: str = "auto", network_name: str = "xab_model",
         compiler_options: list[str] | None = None, timeout: int = 600,
         force: bool = False, detail: str = "compact", distro: str = "Ubuntu",
         refresh: bool = False, cache_inputs: list[str] | None = None,
         arch_json: str = "") -> dict:
    """Execute one compact Vitis AI workflow action."""
    request = validate_request(
        action, work_dir, model, script, args, version, network_name,
        compiler_options, timeout, force, detail, distro, refresh, cache_inputs, arch_json,
    )
    user_args = request["args"]
    options = request["compiler_options"]
    verification = "TARGET_BOARD_INFERENCE_NOT_VERIFIED" if arch_json else "KV260_BOARD_INFERENCE_NOT_VERIFIED"
    declared_inputs = request["cache_inputs"]
    resolved_version = request["version"]
    environment = _inspect_environment(resolved_version, distro, refresh)
    if action == "inspect":
        return _project_inspect(environment, detail)
    required = {
        "analyze": ("analyze",),
        "compile": ("compile", "analyze"),
        "quantize": ("quantize",),
        "pipeline": ("quantize", "compile", "analyze"),
        "smoke": ("quantize",),
    }[action]
    if arch_json:
        required = tuple("compiler" if name == "compile" else name for name in required)
    missing = [name for name in required if not environment["capabilities"].get(name)]
    if missing:
        raise VitisAIError(f"Vitis AI 环境不完整: {', '.join(missing)}")

    if action == "pipeline":
        root = work_dir or str(config.CONFIG_DIR / "vitis_ai" / "pipeline")
        if root.startswith("/"):
            quant_dir = str(PurePosixPath(root) / "quantized")
            compiled_dir = str(PurePosixPath(root) / "compiled")
        else:
            quant_dir = str(Path(root).expanduser().resolve() / "quantized")
            compiled_dir = str(Path(root).expanduser().resolve() / "compiled")
        quantized = flow(
            "quantize", quant_dir, "", script, user_args, resolved_version,
            network_name, options, timeout, force, detail, distro, refresh,
            declared_inputs,
        )
        candidates = [path for path in quantized.get("artifacts", []) if path.casefold().endswith(".xmodel")]
        selected_model = model
        if selected_model:
            if selected_model not in candidates and not Path(selected_model).expanduser().is_file():
                raise VitisAIError("pipeline 指定的 model 不在量化产物中且文件不存在")
        elif len(candidates) == 1:
            selected_model = candidates[0]
        elif not candidates:
            raise VitisAIError("pipeline 量化完成但没有发现 XMODEL")
        else:
            raise VitisAIError("pipeline 发现多个 XMODEL；请用 model 明确选择")
        compiled = flow(
            "compile", compiled_dir, selected_model, "", [], resolved_version,
            network_name, options, timeout, force, detail, distro, refresh, [], arch_json,
        )
        return {
            "status": "success", "action": action, "version": resolved_version,
            "quantize": {
                "cache": quantized.get("cache"),
                "xmodel": selected_model,
            },
            "compile": {
                "cache": compiled.get("cache"),
                "artifacts": compiled.get("artifacts", []),
            },
            "model": compiled.get("model"),
            "process_launches": (
                int(quantized.get("process_launches", 0))
                + int(compiled.get("process_launches", 0))
            ),
            "mcp_calls": 1,
            "limitations": [verification],
            **({"stages": {"quantize": quantized, "compile": compiled}}
               if detail == "standard" else {}),
        }

    wsl = _wsl_executable()
    conda = environment["paths"]["conda"]
    env_name = environment["environment"]
    if action == "analyze":
        model_wsl = _wsl_path(wsl, distro, model, must_exist=True)
        run = _run_dir(action)
        log_path = run / "run.log"
        command = [
            wsl, "-d", distro, "--", conda, "run", "-n", env_name,
            "--no-capture-output", "python", "-c", _ANALYZE_SCRIPT, model_wsl,
        ]
        rc = _run_logged(command, timeout, log_path)
        summary = _parse_model_result(log_path)
        if rc != 0 or not summary:
            raise VitisAIError(
                f"XMODEL 结构校验失败（rc={rc}）: {_tail(log_path)}；日志: {log_path}"
            )
        result = {
            "status": "success", "action": action, "version": resolved_version,
            "model": _project_model(summary, detail), "process_launches": 1,
            "limitations": [verification],
        }
        if detail == "standard":
            result["log"] = str(log_path)
        return result
    if action == "compile":
        model_wsl = _wsl_path(wsl, distro, model, must_exist=True)
        if work_dir:
            output = work_dir
        elif model.startswith("/"):
            output = str(PurePosixPath(model).parent / "xab_vitis_ai" / network_name)
        else:
            output = str(Path(model).expanduser().parent / "xab_vitis_ai" / network_name)
        output_wsl = _wsl_path(wsl, distro, output, must_exist=False)
        arch_bytes = Path(arch_json).expanduser().read_bytes() if arch_json else None
        arch_digest = hashlib.sha256(arch_bytes).hexdigest() if arch_bytes is not None else ""
        fingerprint = _fingerprint_model(
            model, resolved_version, network_name,
            options + (["arch_sha256=" + arch_digest] if arch_digest else []),
        )
        manifest = Path(output).expanduser().resolve() / ".xab-vitis-ai-cache.json" if not output.startswith("/") else None
        cacheable = Path(model).expanduser().is_file()
        if not force and cacheable and manifest and manifest.is_file():
            try:
                cached = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = {}
            artifacts = [Path(item) for item in cached.get("artifacts", [])]
            cached_model = cached.get("model")
            if (cached.get("fingerprint") == fingerprint and artifacts
                    and all(item.is_file() for item in artifacts)
                    and isinstance(cached_model, dict) and cached_model.get("sha256")):
                return {
                    "status": "success", "action": action, "version": resolved_version,
                    "cache": {"hit": True, "fingerprint": fingerprint[:16]},
                    "artifacts": [str(item) for item in artifacts],
                    "model": _project_model(cached_model, detail),
                    "process_launches": 0,
                    "limitations": [verification],
                }
        run = _run_dir(action)
        log_path = run / "run.log"
        driver_path = run / "compile_and_analyze.py"
        driver_path.write_text(_COMPILE_DRIVER, encoding="utf-8")
        selected_arch = environment["paths"]["kv260_arch"]
        if arch_bytes is not None:
            snapshot = run / "arch.json"
            snapshot.write_bytes(arch_bytes)
            selected_arch = _wsl_path(wsl, distro, str(snapshot), must_exist=True)
        driver_wsl = _wsl_path(wsl, distro, str(driver_path), must_exist=True)
        command = [
            wsl, "-d", distro, "--", conda, "run", "-n", env_name,
            "--no-capture-output", "python", driver_wsl,
            environment["paths"]["compiler"], model_wsl,
            selected_arch, output_wsl,
            network_name, *options,
        ]
        rc = _run_logged(command, timeout, log_path)
        if rc != 0:
            raise VitisAIError(f"XMODEL 编译失败（rc={rc}）: {_tail(log_path)}；日志: {log_path}")
        if not output.startswith("/"):
            output_path = Path(output).expanduser().resolve()
            expected = output_path / f"{network_name}.xmodel"
            artifacts = [str(expected)] if expected.is_file() else sorted(
                str(path) for path in output_path.glob("*.xmodel")
            )
        else:
            found = _wsl_artifacts(wsl, distro, output_wsl)
            expected_name = f"{network_name}.xmodel"
            exact = [path for path in found if PurePosixPath(path).name == expected_name]
            artifacts = exact or found
        if not artifacts:
            # The compiler can preserve another extension/version-specific layout.
            if not output.startswith("/"):
                artifacts = sorted(str(path) for path in Path(output).expanduser().resolve().iterdir() if path.is_file() and path.name != ".xab-vitis-ai-cache.json")
        if not artifacts:
            raise VitisAIError(f"编译返回成功但没有发现产物；日志: {log_path}")
        model_summary = _parse_model_result(log_path)
        if not model_summary:
            raise VitisAIError(f"编译产物未通过 XIR 结构校验；日志: {log_path}")
        if manifest:
            manifest.write_text(json.dumps({
                "fingerprint": fingerprint, "artifacts": artifacts,
                "model": model_summary,
            }, separators=(",", ":")), encoding="utf-8")
        result = {
            "status": "success", "action": action, "version": resolved_version,
            "cache": {"hit": False, "fingerprint": fingerprint[:16]},
            "artifacts": artifacts, "model": _project_model(model_summary, detail),
            "process_launches": 1,
            "limitations": [verification],
        }
        if detail == "standard":
            result["log"] = str(log_path)
            result["environment"] = env_name
            result["arch"] = selected_arch
        return result

    output = work_dir or str(config.CONFIG_DIR / "vitis_ai" / action)
    output_wsl = _wsl_path(wsl, distro, output, must_exist=False)
    if action == "smoke":
        smoke_manifest = Path(output).expanduser().resolve() / ".xab-vitis-ai-smoke.json" if not output.startswith("/") else None
        smoke_fingerprint = hashlib.sha256(
            (_SMOKE_SCRIPT + resolved_version).encode("utf-8")
        ).hexdigest()
        quant_info = Path(output).expanduser().resolve() / "quant_info.json" if not output.startswith("/") else None
        if not force and smoke_manifest and quant_info and smoke_manifest.is_file() and quant_info.is_file():
            try:
                cached_smoke = json.loads(smoke_manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached_smoke = {}
            cached_checks = cached_smoke.get("checks", {})
            cached_xmodel = (
                Path(output).expanduser().resolve() / cached_checks.get("xmodel", "")
                if cached_checks.get("xmodel") else None
            )
            if (cached_smoke.get("fingerprint") == smoke_fingerprint
                    and cached_xmodel and cached_xmodel.is_file()):
                return {
                    "status": "success", "action": action,
                    "version": resolved_version,
                    "checks": cached_checks,
                    "artifacts": [str(quant_info), str(cached_xmodel)],
                    "cache": {"hit": True, "fingerprint": smoke_fingerprint[:16]},
                    "process_launches": 0,
                    "limitations": [verification],
                }
        run = _run_dir(action)
        log_path = run / "run.log"
        script_path = run / "nndct_smoke.py"
        script_path.write_text(_SMOKE_SCRIPT, encoding="utf-8")
        script_wsl = _wsl_path(wsl, distro, str(script_path), must_exist=True)
        command = [
            wsl, "-d", distro, "--", "/usr/bin/env",
            f"XAB_VAI_OUTPUT_DIR={output_wsl}", conda, "run", "-n", env_name,
            "--no-capture-output", *_python_script_argv(resolved_version, script_wsl, []),
        ]
    else:
        quant_fingerprint = _fingerprint_quantize(
            script, user_args, resolved_version, declared_inputs
        )
        quant_manifest = (
            Path(output).expanduser().resolve() / ".xab-vitis-ai-quantize.json"
            if quant_fingerprint and not output.startswith("/") else None
        )
        if not force and quant_manifest and quant_manifest.is_file():
            try:
                cached_quant = json.loads(quant_manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached_quant = {}
            cached_artifacts = [Path(path) for path in cached_quant.get("artifacts", [])]
            if (cached_quant.get("fingerprint") == quant_fingerprint
                    and cached_artifacts and all(path.is_file() for path in cached_artifacts)):
                return {
                    "status": "success", "action": action,
                    "version": resolved_version, "output": output,
                    "artifacts": [str(path) for path in cached_artifacts],
                    "cache": {"hit": True, "fingerprint": quant_fingerprint[:16]},
                    "process_launches": 0,
                    "limitations": [verification],
                }
        run = _run_dir(action)
        log_path = run / "run.log"
        script_wsl = _wsl_path(wsl, distro, script, must_exist=True)
        mapped_args = []
        for value in user_args:
            candidate = Path(value).expanduser()
            mapped_args.append(_wsl_path(wsl, distro, value, must_exist=True) if candidate.exists() else value)
        command = [
            wsl, "-d", distro, "--", "/usr/bin/env",
            f"XAB_VAI_OUTPUT_DIR={output_wsl}", conda, "run", "-n", env_name,
            "--no-capture-output", *_python_script_argv(resolved_version, script_wsl, mapped_args),
        ]
    rc = _run_logged(command, timeout, log_path)
    if rc != 0:
        raise VitisAIError(f"Vitis AI {action} 失败（rc={rc}）: {_tail(log_path)}；日志: {log_path}")
    artifacts = []
    if not output.startswith("/"):
        output_path = Path(output).expanduser().resolve()
        artifacts = sorted(
            str(path) for path in output_path.rglob("*")
            if path.is_file() and (
                path.name == "quant_info.json" or path.suffix.casefold() in {".xmodel", ".pth"}
            )
        )[:16]
    else:
        artifacts = _wsl_artifacts(wsl, distro, output_wsl)
    result = {
        "status": "success", "action": action, "version": resolved_version,
        "output": output, "artifacts": artifacts, "process_launches": 1,
        "limitations": [verification],
    }
    if action == "smoke":
        result["checks"] = _parse_smoke_result(log_path)
        if not result["checks"].get("ok"):
            raise VitisAIError(f"NNDCT 自检缺少成功标记；日志: {log_path}")
        result["cache"] = {"hit": False, "fingerprint": smoke_fingerprint[:16]}
        if smoke_manifest:
            smoke_manifest.write_text(json.dumps({
                "fingerprint": smoke_fingerprint, "checks": result["checks"],
            }, separators=(",", ":")), encoding="utf-8")
    else:
        if quant_fingerprint and quant_manifest:
            quant_manifest.write_text(json.dumps({
                "fingerprint": quant_fingerprint, "artifacts": artifacts,
            }, separators=(",", ":")), encoding="utf-8")
            result["cache"] = {"hit": False, "fingerprint": quant_fingerprint[:16]}
        else:
            result["cache"] = {
                "hit": False, "reason": "user_script_dependencies_not_assumed",
            }
    if detail == "standard":
        result["log"] = str(log_path)
        result["environment"] = env_name
        result["output_wsl"] = _host_artifact_path(output_wsl)
    return result
