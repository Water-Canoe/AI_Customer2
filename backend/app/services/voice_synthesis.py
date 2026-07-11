from __future__ import annotations

import os
import subprocess
import threading
import wave
from pathlib import Path
from typing import Any

from app import database
from app.services import content_assets, voice_profiles
from app.video_engine.utils import utils


_MODEL_ID = "openbmb/VoxCPM2"
_model: Any = None
_model_lock = threading.RLock()


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
    try:
        import importlib.util

        installed = bool(importlib.util.find_spec("voxcpm"))
    except (ImportError, ValueError):
        installed = False
    return {
        "provider": "voxcpm2",
        "installed": installed,
        "downloaded": snapshots.is_dir() and any(snapshots.iterdir()),
        "path": str(root),
    }


def _load_voxcpm2() -> Any:
    global _model
    # 大模型只在首次实际合成时加载，避免普通工作台启动就占满显存。
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from voxcpm import VoxCPM
        except ImportError as exc:
            raise RuntimeError("缺少VoxCPM2依赖，请重新安装当前版本的软件") from exc
        model_root = database.get_voice_models_root() / "VoxCPM2"
        model_root.mkdir(parents=True, exist_ok=True)
        _model = VoxCPM.from_pretrained(
            _MODEL_ID,
            cache_dir=str(model_root),
            load_denoiser=False,
            device="auto",
            optimize=os.name != "nt",
        )
        return _model


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
    try:
        model = _load_voxcpm2()
        # VoxCPM推理不是线程安全的，视频和未来数字人任务共用同一把模型锁。
        with _model_lock:
            wav = model.generate(
                text=target_text,
                reference_wav_path=str(prepared_reference),
                prompt_wav_path=str(prepared_reference) if prompt_text else None,
                prompt_text=prompt_text or None,
            )
    finally:
        if remove_reference:
            prepared_reference.unlink(missing_ok=True)
    temp_wav = output.with_suffix(".voxcpm.wav")
    try:
        _write_pcm_wav(temp_wav, wav, int(model.tts_model.sample_rate))
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


def _write_pcm_wav(path: Path, samples: Any, sample_rate: int) -> None:
    import numpy as np

    pcm = (np.clip(np.asarray(samples).reshape(-1), -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(pcm.tobytes())
