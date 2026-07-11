param(
    [string]$TorchIndex = "https://download.pytorch.org/whl/cu128",
    [string]$WheelDir = ""
)

$ErrorActionPreference = "Stop"

# Install only the VoxCPM inference path; its unused Gradio UI conflicts with this app's FastAPI version.
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Backend virtual environment not found: $Python"
}

$WheelDir = if ($WheelDir) { $WheelDir } else { Join-Path $ProjectRoot "output\voxcpm_downloads" }
$TorchWheel = Join-Path $WheelDir "torch-2.11.0+cu128-cp311-cp311-win_amd64.whl"
$TorchaudioWheel = Join-Path $WheelDir "torchaudio-2.11.0+cu128-cp311-cp311-win_amd64.whl"
if ((Test-Path -LiteralPath $TorchWheel -PathType Leaf) -and (Test-Path -LiteralPath $TorchaudioWheel -PathType Leaf)) {
    # Prefer verified local wheels so the multi-gigabyte CUDA runtime is never downloaded twice.
    & $Python -m pip install $TorchWheel $TorchaudioWheel "torchcodec==0.14.0"
}
else {
    # Download only when local wheels have not been prepared.
    & $Python -m pip install --timeout 600 --retries 10 --index-url $TorchIndex `
        "torch==2.11.0+cu128" `
        "torchaudio==2.11.0+cu128" `
        "torchcodec==0.14.0"
}
if ($LASTEXITCODE -ne 0) { throw "CUDA PyTorch installation failed" }

& $Python -m pip install `
    "transformers==5.13.0" `
    "einops==0.8.2" `
    "inflect==7.5.0" `
    "addict==2.4.0" `
    "wetext==0.1.4" `
    "modelscope==1.38.1" `
    "huggingface-hub==1.23.0" `
    "simplejson==4.1.1" `
    "sortedcontainers==2.4.0" `
    "soundfile==0.14.0" `
    "librosa==0.11.0" `
    "safetensors==0.8.0" `
    "argbind==0.3.9"
if ($LASTEXITCODE -ne 0) { throw "VoxCPM inference dependencies installation failed" }

& $Python -m pip install --no-deps "voxcpm==2.0.3"
if ($LASTEXITCODE -ne 0) { throw "VoxCPM installation failed" }

# Restore the main project's dependency pins after installing the isolated inference dependencies.
& $Python -m pip install -r (Join-Path $ProjectRoot "backend\requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Project dependency constraints restoration failed" }

& $Python -c "import torch, voxcpm; assert torch.cuda.is_available(), 'CUDA is unavailable'; print('VoxCPM2 ready:', torch.__version__, torch.cuda.get_device_name(0))"
if ($LASTEXITCODE -ne 0) { throw "VoxCPM CUDA verification failed" }
