"""Isolated Cronet C ABI worker for CapCut PC background mode (ported from CapCap).

Uses the installed ``sscronet.dll`` from CapCut Desktop inside a separate child
process without starting ``CapCut.exe``. Upload VOD transfers and audio
downloads still use standard HTTPS; API control requests to
``editor-api-sg.capcutapi.com`` and ``lv-pc-api-sinfonlinec.ulikecam.com`` are
dispatched over the native TTNet/Cronet engine when ``capcut_backend == "pc"``.
"""

from __future__ import annotations

import atexit
import ctypes as C
import logging
import multiprocessing
import os
import queue
import re
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

P, S, I, U = C.c_void_p, C.c_char_p, C.c_int, C.c_uint64
EXEC = C.CFUNCTYPE(None, P, P)
REDIRECT = C.CFUNCTYPE(None, P, P, P, S)
RESPONSE = C.CFUNCTYPE(None, P, P, P)
READ = C.CFUNCTYPE(None, P, P, P, P, U)
FAILED = C.CFUNCTYPE(None, P, P, P, P)
LENGTH = C.CFUNCTYPE(C.c_int64, P)
UPLOAD_READ = C.CFUNCTYPE(None, P, P, P)
REWIND = C.CFUNCTYPE(None, P, P)
CLOSE = C.CFUNCTYPE(None, P)

API_HOSTS = frozenset({"editor-api-sg.capcutapi.com", "lv-pc-api-sinfonlinec.ulikecam.com"})
_BRIDGES: dict[tuple[str, str], _NativeBridge] = {}
_BRIDGES_LOCK = threading.Lock()


def find_capcut_pc_dll(configured_path: str | None = None) -> Path:
    """Locate ``sscronet.dll`` from an explicit path, standard folders, or the Windows registry."""
    if os.name != "nt":
        raise RuntimeError("CapCut PC background mode requires Windows.")
    configured = (
        configured_path if configured_path is not None else os.environ.get("CAPCUT_PC_PATH", "")
    ).strip()
    roots: list[Path] = []
    if configured:
        path = Path(os.path.expandvars(configured)).expanduser()
        if path.name.lower() == "sscronet.dll":
            if path.is_file():
                return path.resolve()
            raise FileNotFoundError(f"CapCut PC library not found: {path}")
        if path.suffix.lower() == ".exe":
            path = path.parent
        roots.append(path)
    else:
        for key in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            val = os.environ.get(key)
            if val:
                roots.append(Path(val) / "CapCut")
        try:
            import winreg  # noqa: PLC0415

            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for branch in (
                    r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CapCut",
                    r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\CapCut",
                ):
                    try:
                        with winreg.OpenKey(hive, branch) as reg_key:
                            location = winreg.QueryValueEx(reg_key, "InstallLocation")[0]
                            if location:
                                roots.append(Path(location))
                    except OSError:
                        pass
        except ImportError:
            pass

    candidates: list[Path] = []
    for root in roots:
        for pattern in ("sscronet.dll", "*/sscronet.dll", "Apps/*/sscronet.dll"):
            try:
                candidates.extend(p for p in root.glob(pattern) if p.is_file())
            except OSError:
                continue
    if not candidates:
        raise FileNotFoundError(
            "CapCut PC library sscronet.dll was not found. Install CapCut PC or configure its path in Settings."
        )

    def _version_key(path: Path) -> tuple[tuple[int, ...], str]:
        return tuple(int(n) for n in re.findall(r"\d+", path.parent.name)), str(path)

    return max(candidates, key=_version_key).resolve()


class CronetEngine:
    def __init__(self, dll_path: str, device: dict[str, Any]) -> None:
        self.directory = os.add_dll_directory(os.path.dirname(dll_path))
        self.lib = C.CDLL(dll_path)
        self._bind()
        self.tasks: queue.Queue[Any] = queue.Queue()
        self.executor_callback = EXEC(lambda _executor, task: self.tasks.put(task))
        self.executor = self.Executor_CreateWith(self.executor_callback)
        self.thread = threading.Thread(target=self._execute, daemon=True)
        self.thread.start()
        self.cache = tempfile.TemporaryDirectory(prefix="sva_pc_cronet_", ignore_cleanup_errors=True)
        tt = self.TTNetParams_Create()
        params = self.EngineParams_Create()
        self.engine = self.Engine_Create()
        try:
            values = {
                "app_id": device["aid"],
                "app_name": device["app_name"],
                "device_id": device["device_id"],
                "uuid": device["device_id"],
                "device_platform": "windows",
                "device_type": "pc",
                "channel": device["channel"],
                "version_code": device["appvr"],
                "update_version_code": device["appvr"],
                "domain_httpdns": "dig.bdurl.net",
                "tnc_host_first": "tnc-sg.capcut.com",
                "tnc_host_second": "tnc-sg.capcut.com",
                "tnc_host_third": "tnc-sg.capcut.com",
                "domain_boe": "boe.bytedance.net",
                "domain_netlog": "log-sg.capcut.com",
                "is_main_process": "true",
            }
            for name, value in values.items():
                getattr(self, f"TTNetParams_{name}_set")(tt, str(value).encode())
            self.EngineParams_storage_path_set(params, self.cache.name.encode("utf-8"))
            self.EngineParams_enable_check_result_set(params, False)
            self.EngineParams_enable_quic_set(params, False)
            self.EngineParams_enable_http2_set(params, True)
            self.EngineParams_ttnet_params_set(params, tt)
            result = self.Engine_StartWithParams(self.engine, params)
            if result:
                raise RuntimeError(f"CapCut PC Cronet engine initialization failed (code={result})")
        finally:
            self.EngineParams_Destroy(params)
            self.TTNetParams_Destroy(tt)

    def _bind(self) -> None:
        signatures: dict[str, tuple[Any, list[Any]]] = {
            "Executor_CreateWith": (P, [EXEC]),
            "Runnable_Run": (None, [P]),
            "Runnable_Destroy": (None, [P]),
            "Engine_StartWithParams": (I, [P, P]),
            "Engine_Shutdown": (I, [P]),
            "Engine_Destroy": (None, [P]),
            "Executor_Destroy": (None, [P]),
            "EngineParams_ttnet_params_set": (None, [P, P]),
            "EngineParams_storage_path_set": (None, [P, S]),
            "UrlRequestCallback_CreateWith": (P, [REDIRECT, RESPONSE, READ, RESPONSE, FAILED, RESPONSE]),
            "UploadDataProvider_CreateWith": (P, [LENGTH, UPLOAD_READ, REWIND, CLOSE]),
            "UrlRequestParams_http_method_set": (None, [P, S]),
            "UrlRequestParams_request_headers_add": (None, [P, P]),
            "UrlRequestParams_upload_data_provider_set": (None, [P, P]),
            "UrlRequestParams_upload_data_provider_executor_set": (None, [P, P]),
            "UrlRequest_InitWithParams": (I, [P, P, S, P, P, P]),
            "UrlRequest_Start": (I, [P]),
            "UrlRequest_Cancel": (None, [P]),
            "UrlRequest_FollowRedirect": (I, [P]),
            "UrlRequest_Read": (I, [P, P]),
            "Buffer_InitWithAlloc": (None, [P, U]),
            "Buffer_GetData": (P, [P]),
            "Buffer_GetSize": (U, [P]),
            "UrlResponseInfo_http_status_code_get": (I, [P]),
            "UrlResponseInfo_all_headers_list_size": (C.c_uint32, [P]),
            "UrlResponseInfo_all_headers_list_at": (P, [P, C.c_uint32]),
            "Error_message_get": (S, [P]),
            "UploadDataSink_OnReadSucceeded": (None, [P, U, C.c_bool]),
            "UploadDataSink_OnRewindSucceeded": (None, [P]),
        }
        for kind in ("Engine", "EngineParams", "TTNetParams", "HttpHeader", "Buffer", "UrlRequest", "UrlRequestParams"):
            signatures[kind + "_Create"] = (P, [])
        for kind in (
            "EngineParams",
            "TTNetParams",
            "HttpHeader",
            "Buffer",
            "UrlRequest",
            "UrlRequestParams",
            "UrlRequestCallback",
            "UploadDataProvider",
        ):
            signatures[kind + "_Destroy"] = (None, [P])
        for name in (
            "app_id",
            "app_name",
            "device_id",
            "uuid",
            "device_platform",
            "device_type",
            "channel",
            "version_code",
            "update_version_code",
            "domain_httpdns",
            "tnc_host_first",
            "tnc_host_second",
            "tnc_host_third",
            "domain_boe",
            "domain_netlog",
            "is_main_process",
        ):
            signatures[f"TTNetParams_{name}_set"] = (None, [P, S])
        for name in ("enable_check_result", "enable_quic", "enable_http2"):
            signatures[f"EngineParams_{name}_set"] = (None, [P, C.c_bool])
        for name in ("name", "value"):
            signatures[f"HttpHeader_{name}_set"] = (None, [P, S])
            signatures[f"HttpHeader_{name}_get"] = (S, [P])
        for name, (result, args) in signatures.items():
            function = getattr(self.lib, "Cronet_" + name)
            function.restype, function.argtypes = result, args
            setattr(self, name, function)

    def _execute(self) -> None:
        while True:
            task = self.tasks.get()
            if task is None:
                return
            if callable(task):
                task()
            else:
                try:
                    self.Runnable_Run(task)
                finally:
                    self.Runnable_Destroy(task)

    def close(self) -> None:
        self.Engine_Shutdown(self.engine)
        self.Engine_Destroy(self.engine)
        self.tasks.put(None)
        self.thread.join(2)
        self.Executor_Destroy(self.executor)
        self.cache.cleanup()

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        done = threading.Event()
        chunks: list[bytes] = []
        headers: dict[str, str] = {}
        status, error, offset = 0, "", 0
        buffer_owned_by_cronet = False
        buffer = self.Buffer_Create()
        self.Buffer_InitWithAlloc(buffer, 65536)
        body = message.get("body") or b""
        source = C.create_string_buffer(body)

        def terminal(*_args: Any) -> None:
            self.tasks.put(done.set)

        def failed(_cb: Any, _req: Any, _info: Any, err: Any) -> None:
            nonlocal error
            error = (self.Error_message_get(err) or b"Unknown Cronet error").decode("utf-8", "replace")
            terminal()

        def response(_cb: Any, req: Any, info: Any) -> None:
            nonlocal status, buffer_owned_by_cronet
            status = self.UrlResponseInfo_http_status_code_get(info)
            for index in range(self.UrlResponseInfo_all_headers_list_size(info)):
                item = self.UrlResponseInfo_all_headers_list_at(info, index)
                name, value = self.HttpHeader_name_get(item), self.HttpHeader_value_get(item)
                headers[(name or b"").decode("latin-1")] = (value or b"").decode("latin-1")
            buffer_owned_by_cronet = self.UrlRequest_Read(req, buffer) == 0

        def read(_cb: Any, req: Any, _info: Any, buf: Any, size: int) -> None:
            nonlocal buffer_owned_by_cronet
            buffer_owned_by_cronet = False
            if size:
                chunks.append(C.string_at(self.Buffer_GetData(buf), size))
            buffer_owned_by_cronet = self.UrlRequest_Read(req, buf) == 0

        def upload_read(_provider: Any, sink: Any, buf: Any) -> None:
            nonlocal offset
            size = min(len(body) - offset, self.Buffer_GetSize(buf))
            if size:
                C.memmove(self.Buffer_GetData(buf), C.addressof(source) + offset, size)
            offset += size
            self.UploadDataSink_OnReadSucceeded(sink, size, False)

        def rewind(_provider: Any, sink: Any) -> None:
            nonlocal offset
            offset = 0
            self.UploadDataSink_OnRewindSucceeded(sink)

        callbacks = [
            REDIRECT(lambda _cb, req, _info, _url: self.UrlRequest_FollowRedirect(req)),
            RESPONSE(response),
            READ(read),
            RESPONSE(terminal),
            FAILED(failed),
            RESPONSE(terminal),
        ]
        upload_callbacks = [
            LENGTH(lambda _self: len(body)),
            UPLOAD_READ(upload_read),
            REWIND(rewind),
            CLOSE(lambda _self: None),
        ]
        callback = self.UrlRequestCallback_CreateWith(*callbacks)
        params = self.UrlRequestParams_Create()
        request_obj = self.UrlRequest_Create()
        upload = None
        started = False
        try:
            self.UrlRequestParams_http_method_set(params, message["method"].encode("ascii"))
            for name, value in message.get("headers", {}).items():
                item = self.HttpHeader_Create()
                try:
                    self.HttpHeader_name_set(item, str(name).encode("ascii"))
                    self.HttpHeader_value_set(item, str(value).encode("latin-1"))
                    self.UrlRequestParams_request_headers_add(params, item)
                finally:
                    self.HttpHeader_Destroy(item)
            if body:
                upload = self.UploadDataProvider_CreateWith(*upload_callbacks)
                self.UrlRequestParams_upload_data_provider_set(params, upload)
                self.UrlRequestParams_upload_data_provider_executor_set(params, self.executor)
            result = self.UrlRequest_InitWithParams(
                request_obj, self.engine, message["url"].encode(), params, callback, self.executor
            )
            if result:
                raise RuntimeError(f"Cronet request initialization failed (code={result})")
            result = self.UrlRequest_Start(request_obj)
            if result:
                raise RuntimeError(f"Cronet request start failed (code={result})")
            started = True
            if not done.wait(message["timeout"]):
                self.UrlRequest_Cancel(request_obj)
                if not done.wait(5):
                    os._exit(70)
                return {"error_type": "timeout", "error": "CapCut PC request timed out"}
            if error:
                return {"error_type": "connection", "error": error}
            return {"status": status, "headers": headers, "body": b"".join(chunks)}
        finally:
            if not started or done.is_set():
                self.UrlRequest_Destroy(request_obj)
                self.UrlRequestParams_Destroy(params)
                if upload:
                    self.UploadDataProvider_Destroy(upload)
                self.UrlRequestCallback_Destroy(callback)
                if not buffer_owned_by_cronet:
                    self.Buffer_Destroy(buffer)


def run_worker(connection: Any, dll_path: str, device: dict[str, Any]) -> None:
    engine = None
    try:
        engine = CronetEngine(dll_path, device)
        connection.send({"ready": True})
        send_lock = threading.Lock()

        def execute(message: dict[str, Any]) -> None:
            try:
                result = engine.request(message)
            except Exception as exc:  # noqa: BLE001
                result = {"error_type": "native", "error": f"{type(exc).__name__}: {exc}"}
            result["id"] = message["id"]
            with send_lock:
                connection.send(result)

        with ThreadPoolExecutor(max_workers=32, thread_name_prefix="capcut-pc") as pool:
            while True:
                message = connection.recv()
                if message is None:
                    break
                pool.submit(execute, message)
    except (EOFError, BrokenPipeError, OSError):
        pass
    except Exception as exc:  # noqa: BLE001
        try:
            connection.send({"error": f"{type(exc).__name__}: {exc}"})
        except (OSError, EOFError):
            pass
    finally:
        if engine is not None:
            engine.close()
        connection.close()


class _NativeBridge:
    def __init__(self, dll_path: Path, device: dict[str, Any]) -> None:
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.lock = threading.Lock()
        self.pending: dict[str, tuple[threading.Event, dict[str, Any]]] = {}
        self.error = ""
        self.closed = False
        self.process = context.Process(
            target=run_worker,
            args=(child, str(dll_path), device),
            daemon=True,
            name="CapCut-PC-Bridge",
        )
        try:
            self.process.start()
            child.close()
            if not self.connection.poll(30):
                raise RuntimeError("CapCut PC background process did not initialize within 30 seconds")
            message = self.connection.recv()
            if not message.get("ready"):
                detail = message.get("error") or "Unknown initialization error"
                raise RuntimeError(f"CapCut PC library initialization failed (library={dll_path}): {detail}")
        except BaseException:
            child.close()
            self.close()
            raise
        log.info("[CapCut PC] Native background process ready pid=%s library=%s", self.process.pid, dll_path)
        self.reader = threading.Thread(target=self._receive, daemon=True, name="capcut-pc-replies")
        self.reader.start()

    def _receive(self) -> None:
        try:
            while True:
                message = self.connection.recv()
                with self.lock:
                    pending = self.pending.get(message.get("id"))
                    if pending:
                        pending[1].update(message)
                        pending[0].set()
        except (OSError, EOFError, TypeError) as exc:
            with self.lock:
                self.error = f"CapCut PC background process disconnected (exit={self.process.exitcode}, {type(exc).__name__})"
                for event, result in self.pending.values():
                    result.update(error_type="connection", error=self.error)
                    event.set()

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        event = threading.Event()
        result: dict[str, Any] = {}
        with self.lock:
            if self.closed or self.error or not self.process.is_alive():
                raise RuntimeError(self.error or "CapCut PC background process is unavailable")
            self.pending[request_id] = (event, result)
            try:
                self.connection.send(dict(message, id=request_id))
            except (OSError, EOFError) as exc:
                self.pending.pop(request_id, None)
                raise RuntimeError(f"Cannot send request to CapCut PC: {exc}") from exc
        try:
            if not event.wait(float(message.get("timeout", 60.0)) + 15.0):
                self.close()
                raise TimeoutError(f"CapCut PC background request timed out ({message.get('timeout')}s)")
            error_type = result.get("error_type")
            if error_type:
                error = result.get("error", "Unknown native transport error")
                if error_type == "timeout":
                    raise TimeoutError(error)
                self.close()
                raise RuntimeError(f"CapCut PC native request failed: {error}")
            return result
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.process.pid and self.process.is_alive():
            try:
                with self.lock:
                    self.connection.send(None)
            except (OSError, EOFError):
                pass
            self.process.join(2)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(2)
        self.connection.close()


def get_bridge(device: dict[str, Any], dll_path: Path) -> _NativeBridge:
    key = (str(dll_path), str(device.get("aid", "")))
    with _BRIDGES_LOCK:
        bridge = _BRIDGES.get(key)
        if bridge is None or bridge.closed or bridge.error or not bridge.process.is_alive():
            if bridge:
                bridge.close()
            bridge = _NativeBridge(dll_path, device)
            _BRIDGES[key] = bridge
        return bridge


def close_capcut_pc_bridges() -> None:
    with _BRIDGES_LOCK:
        for bridge in _BRIDGES.values():
            bridge.close()
        _BRIDGES.clear()


atexit.register(close_capcut_pc_bridges)


def native_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: bytes = b"",
    timeout: float = 60.0,
    device: dict[str, Any],
    dll_path: Path,
) -> tuple[int, dict[str, str], bytes]:
    """Execute one HTTP request via the CapCut PC Cronet bridge."""
    bridge = get_bridge(device, dll_path)
    reply = bridge.request(
        {
            "method": method.upper(),
            "url": url,
            "headers": dict(headers or {}),
            "body": body,
            "timeout": float(timeout),
        }
    )
    return int(reply.get("status", 0)), dict(reply.get("headers") or {}), bytes(reply.get("body") or b"")


__all__ = [
    "API_HOSTS",
    "close_capcut_pc_bridges",
    "find_capcut_pc_dll",
    "get_bridge",
    "native_request",
]
