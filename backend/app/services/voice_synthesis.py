from __future__ import annotations

import atexit
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from app import database
from app.services import content_assets, voice_profiles, voice_runtime
from app.video_engine.utils import utils


_worker: subprocess.Popen[str] | None = None
_worker_log: Any = None
_worker_lock = threading.RLock()


def synthesize_profile(
    profile_id: str,
    text: str,
    output_file: str,
    *,
    voice_rate: float = 1.0,
    voice_volume: float = 1.0,
) -> None:
    profile = voice_profiles.get_profile(profile_id)
    if profile["provider"] != "voxcpm2":
        raise ValueError("该克隆音色引擎尚未接入语音合成")
    reference = content_assets.resolve_asset_path(str(profile["reference_asset"]["relative_path"]))
    _synthesize_voxcpm2(profile, str(text or "").strip(), reference, Path(output_file), voice_rate, voice_volume)


def model_status() -> dict[str, Any]:
    root = database.get_voice_models_root() / "VoxCPM2"
    snapshots = root / "models--openbmb--VoxCPM2" / "snapshots"
    value = voice_runtime.status()
    value.update(
        {
            "provider": "voxcpm2",
            "downloaded": snapshots.is_dir() and any(snapshots.iterdir()),
            "model_path": str(root),
        }
    )
    return value


def _synthesize_voxcpm2(
    profile: dict[str, Any],
    text: str,
    reference: Path,
    output: Path,
    voice_rate: float,
    voice_volume: float,
) -> None:
    if not text:
        raise ValueError("待合成文字不能为空")
    output.parent.mkdir(parents=True, exist_ok=True)
    style = str(profile.get("style_prompt") or "").translate(str.maketrans("", "", "()（）")).strip()
    target_text = f"({style}){text}" if style else text
    prompt_text = str(profile.get("prompt_text") or "").strip()
    prepared_reference, remove_reference = _prepare_reference_audio(reference, output)
    temp_wav = output.with_suffix(".voxcpm.wav")
    try:
        _request_worker(
            {
                "model_root": str(database.get_voice_models_root() / "VoxCPM2"),
                "reference": str(prepared_reference),
                "text": target_text,
                "prompt_text": prompt_text,
                "output": str(temp_wav),
            }
        )
        command = [
            utils.get_ffmpeg_binary(), "-y", "-i", str(temp_wav),
            "-filter:a", f"atempo={max(0.5, min(2.0, float(voice_rate or 1.0))):.3f},volume={max(0.0, float(voice_volume if voice_volume is not None else 1.0)):.3f}",
            str(output),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError(f"VoxCPM2音频转换失败：{(result.stderr or result.stdout or '').strip()}")
    finally:
        temp_wav.unlink(missing_ok=True)
        if remove_reference:
            prepared_reference.unlink(missing_ok=True)


def _request_worker(payload: dict[str, Any]) -> dict[str, Any]:
    with _worker_lock:
        worker = _ensure_worker()
        if worker.stdin is None or worker.stdout is None:
            raise RuntimeError("音色克隆组件通信通道不可用")
        worker.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        worker.stdin.flush()
        received: list[str] = []

        # readline单独等待，超时后终止异常的推理进程。
        reader = threading.Thread(target=lambda: received.append(worker.stdout.readline()), daemon=True)
        reader.start()
        reader.join(timeout=900)
        if reader.is_alive():
            _stop_worker()
            raise RuntimeError("音色克隆组件响应超时")
        if not received or not received[0]:
            _stop_worker()
            raise RuntimeError("音色克隆组件意外退出")
        try:
            response = json.loads(received[0])
        except ValueError as exc:
            _stop_worker()
            raise RuntimeError("音色克隆组件返回了无效数据") from exc
        if not isinstance(response, dict) or not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "音色克隆失败") if isinstance(response, dict) else "音色克隆失败")
        return response


def _ensure_worker() -> subprocess.Popen[str]:
    global _worker, _worker_log
    if _worker is not None and _worker.poll() is None:
        return _worker
    _stop_worker()
    executable = voice_runtime.worker_executable()
    if executable is None:
        raise RuntimeError("音色克隆组件未安装")
    log_path = database.get_data_root() / "voxcpm_runtime.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _worker_log = log_path.open("a", encoding="utf-8")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        _worker = subprocess.Popen(
            [str(executable), "--serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=_worker_log,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=creation_flags,
        )
    except OSError as exc:
        _stop_worker()
        raise RuntimeError("音色克隆组件启动失败，请重新安装组件") from exc
    return _worker


def _stop_worker() -> None:
    global _worker, _worker_log
    worker, _worker = _worker, None
    if worker is not None and worker.poll() is None:
        worker.terminate()
        try:
            worker.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker.kill()
    if _worker_log is not None:
        _worker_log.close()
        _worker_log = None


def _prepare_reference_audio(reference: Path, output: Path) -> tuple[Path, bool]:
    if reference.suffix.lower() != ".webm":
        return reference, False
    prepared = output.with_suffix(".reference.wav")
    command = [
        utils.get_ffmpeg_binary(), "-y", "-i", str(reference),
        "-ar", "16000", "-ac", "1", str(prepared),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not prepared.is_file() or prepared.stat().st_size <= 0:
        prepared.unlink(missing_ok=True)
        raise RuntimeError(f"录音格式转换失败：{(result.stderr or result.stdout or '').strip()}")
    return prepared, True


atexit.register(_stop_worker)
