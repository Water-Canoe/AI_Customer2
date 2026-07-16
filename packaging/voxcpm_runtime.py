from __future__ import annotations

import contextlib
import json
import sys
import wave
from pathlib import Path
from typing import Any


MODEL_ID = "openbmb/VoxCPM2"
_model: Any = None
_model_root = ""


def _load_model(model_root: str) -> Any:
    global _model, _model_root
    if _model is not None and _model_root == model_root:
        return _model
    from voxcpm import VoxCPM

    root = Path(model_root)
    root.mkdir(parents=True, exist_ok=True)
    # 第一次合成时加载，后续请求复用同一模型进程。
    with contextlib.redirect_stdout(sys.stderr):
        _model = VoxCPM.from_pretrained(
            MODEL_ID,
            cache_dir=str(root),
            load_denoiser=False,
            device="auto",
            optimize=sys.platform != "win32",
        )
    _model_root = model_root
    return _model


def _write_wav(path: Path, samples: Any, sample_rate: int) -> None:
    import numpy as np

    pcm = (np.clip(np.asarray(samples).reshape(-1), -1.0, 1.0) * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(pcm.tobytes())


def _handle(payload: dict[str, Any]) -> dict[str, Any]:
    model_root = str(payload.get("model_root") or "").strip()
    reference = Path(str(payload.get("reference") or ""))
    output = Path(str(payload.get("output") or ""))
    text = str(payload.get("text") or "").strip()
    prompt_text = str(payload.get("prompt_text") or "").strip()
    if not model_root or not reference.is_file() or not output.name or not text:
        raise ValueError("音色克隆请求参数不完整")
    model = _load_model(model_root)
    with contextlib.redirect_stdout(sys.stderr):
        samples = model.generate(
            text=text,
            reference_wav_path=str(reference),
            prompt_wav_path=str(reference) if prompt_text else None,
            prompt_text=prompt_text or None,
        )
    _write_wav(output, samples, int(model.tts_model.sample_rate))
    return {"ok": True, "output": str(output)}


def serve() -> None:
    for line in sys.stdin:
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("请求格式无效")
            response = _handle(payload)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    if "--serve" not in sys.argv:
        raise SystemExit("VoxCPM runtime must be started with --serve")
    serve()
