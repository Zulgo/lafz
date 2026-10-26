# Third-party notices

Murmur's own code is MIT licensed (see [LICENSE](LICENSE)). It bundles or downloads the following:

## Bundled in this repository

| Component | File | License |
| --- | --- | --- |
| [Silero VAD](https://github.com/snakers4/silero-vad) | `assets/silero_vad.onnx` | MIT, © Silero Team |

## Downloaded at first use (not part of this repository)

| Model | Used for | License |
| --- | --- | --- |
| [Whisper large-v3-turbo / small.en](https://github.com/openai/whisper) (MLX and CTranslate2 conversions) | Speech-to-text | MIT |
| [NVIDIA Parakeet TDT 0.6B v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) | Speech-to-text | CC-BY-4.0 |
| [Qwen2.5 1.5B Instruct](https://ollama.com/library/qwen2.5) via Ollama | Optional text cleanup | Apache 2.0 |

## Python libraries

Installed by `uv sync` from PyPI under their own licenses, including mlx-whisper, parakeet-mlx, faster-whisper, onnx-asr,
onnxruntime, sounddevice, numpy, pynput and PyObjC. See `uv.lock` for exact versions.
