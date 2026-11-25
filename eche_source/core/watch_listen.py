# core/watch_listen.py
# watch_video's ears: what is said in a clip, as timestamped lines.
#
# ffmpeg pulls the first sound track out as 16 kHz mono WAV, capped to the
# same ten-minute window the frames come from, and faster-whisper transcribes
# it on the CPU. CPU on purpose: the owner's GPU is an AMD card on Windows and
# CTranslate2 has no ROCm build there, and the GPU/Ollama is shared with
# Waerhouse anyway. int8, a bounded thread count, a deadline.
#
# faster-whisper is OPTIONAL (requirements-whisper.txt, installed by BUILD.bat
# when it can be). It is imported lazily: when it is missing the watch still
# works and the text says the sound was not transcribed. The model downloads
# on first use into the data folder (models/whisper); the log names the model
# and that folder only. Nothing here makes or posts audio, pictures or video.

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field

from core.debuglog import dprint

DEFAULT_MODEL = "small"          # "base" is faster, "small" hears better; both int8 on the CPU
MODELS = ("tiny", "base", "small")
COMPUTE_TYPE = "int8"
DEFAULT_THREADS = 8              # of the 5950X's 16 cores; Eche and Waerhouse keep the rest
MAX_THREADS = 12
AUDIO_SECONDS = 600              # same window as the frames (tools_watch.MAX_SECONDS)
EXTRACT_TIMEOUT_S = 40
LOAD_WAIT_S = 45                 # first use downloads ~0.5 GB (small); the watch does not wait past this
TRANSCRIBE_DEADLINE_S = 120
NO_SPEECH_PROB = 0.6             # a segment Whisper itself thinks is not speech is dropped
TRANSCRIPT_CHARS = 600
SEGMENT_CHARS = 200

# Plain words for the result and the log. Never paths, urls, tokens or speech.
HEARD = "heard"            # transcript lines (may be empty: no speech)
NO_AUDIO = "no_audio"      # the clip has no sound track
NOT_INSTALLED = "not_installed"
OFF = "off"
LOADING = "loading"        # the model is still downloading/loading in the background
FAILED = "failed"

_model_lock = threading.Lock()
# One transcription at a time, even if a timed-out watch left its thread running.
_busy = threading.Lock()
_model = None
_model_name = ""
_loading: threading.Thread | None = None
_load_error = ""


@dataclass
class Heard:
    state: str
    segments: list[dict] = field(default_factory=list)  # [{"start": s, "end": s, "text": str}]
    cut: bool = False   # the deadline stopped the transcript early
    note: str = ""      # short reason for the log ("timeout", an exception name)


def model_name() -> str:
    """ECHE_WATCH_WHISPER: tiny / base / small (default small), or off."""
    raw = (os.environ.get("ECHE_WATCH_WHISPER") or "").strip().lower()
    if raw in ("0", "off", "false", "no", "none"):
        return ""
    return raw if raw in MODELS else DEFAULT_MODEL


def _threads() -> int:
    try:
        value = int(os.environ.get("ECHE_WATCH_WHISPER_THREADS") or DEFAULT_THREADS)
    except ValueError:
        value = DEFAULT_THREADS
    return max(1, min(MAX_THREADS, value))


def installed() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        return False


def model_folder() -> str:
    try:
        from core.paths import user_dir

        root = user_dir()
    except Exception:
        root = os.path.join(os.path.expanduser("~"), ".eche")
    return os.path.join(root, "models", "whisper")


def _downloaded(folder: str, name: str) -> bool:
    """huggingface_hub cache layout: models--Systran--faster-whisper-<name>/snapshots/<rev>/model.bin."""
    base = os.path.join(folder, f"models--Systran--faster-whisper-{name}", "snapshots")
    try:
        return any(os.path.isfile(os.path.join(base, rev, "model.bin")) for rev in os.listdir(base))
    except OSError:
        return False


def _load(name: str) -> None:
    """Load (downloading on first use) in a worker thread. Sets _model or _load_error."""
    global _model, _model_name, _load_error
    try:
        # A windowed (frozen) Eche has no stderr for progress bars to write to.
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        from faster_whisper import WhisperModel

        folder = model_folder()
        os.makedirs(folder, exist_ok=True)
        if not _downloaded(folder, name):
            dprint(f"[watch_video] whisper: downloading model '{name}' to {folder} (first use)")
        started = time.monotonic()
        model = WhisperModel(
            name, device="cpu", compute_type=COMPUTE_TYPE, cpu_threads=_threads(),
            num_workers=1, download_root=folder,
        )
        with _model_lock:
            _model, _model_name, _load_error = model, name, ""
        dprint(f"[watch_video] whisper: model '{name}' ready on cpu/{COMPUTE_TYPE} "
               f"in {time.monotonic() - started:.1f}s")
    except Exception as exc:
        with _model_lock:
            _load_error = type(exc).__name__
        dprint(f"[watch_video] whisper: model '{name}' failed to load: {type(exc).__name__}")


def get_model(name: str, wait_s: float = LOAD_WAIT_S):
    """The loaded model, or None while it is still loading (the load keeps going for next time)."""
    global _loading, _load_error
    with _model_lock:
        if _model is not None and _model_name == name:
            return _model
        if _loading is None or not _loading.is_alive():
            _load_error = ""
            _loading = threading.Thread(target=_load, args=(name,), name="eche-whisper-load", daemon=True)
            _loading.start()
        thread = _loading
    thread.join(max(0.0, wait_s))
    with _model_lock:
        if _model is not None and _model_name == name:
            return _model
        if _load_error:
            raise RuntimeError(_load_error)
    return None


def extract_audio(ffmpeg: str, video_path: str, wav_path: str) -> bool:
    """First sound track -> 16 kHz mono WAV, at most AUDIO_SECONDS. False when there is none."""
    from core.tools_watch import _run

    proc = _run(
        [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", video_path,
         "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-t", str(AUDIO_SECONDS),
         "-f", "wav", wav_path],
        EXTRACT_TIMEOUT_S,
    )
    # A WAV header alone is 44 bytes; anything under ~0.1 s of sound is nothing to hear.
    return proc.returncode == 0 and os.path.isfile(wav_path) and os.path.getsize(wav_path) > 44 + 3200


_SPACES = re.compile(r"\s+")


def _samples(wav_path: str):
    """16 kHz mono 16-bit WAV -> float32 samples. faster-whisper then skips its own PyAV decode
    (PyAV 19 dropped an argument faster-whisper 1.2.1 still passes)."""
    import wave

    import numpy as np

    with wave.open(wav_path, "rb") as handle:
        if handle.getsampwidth() != 2 or handle.getnchannels() != 1:
            raise ValueError("unexpected wav layout")
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def run_model(model, wav_path: str, deadline_s: float = TRANSCRIBE_DEADLINE_S) -> tuple[list[dict], bool]:
    """([{"start","end","text"}], cut). Segments are read lazily, so the deadline really stops the work."""
    segments, _info = model.transcribe(
        _samples(wav_path), beam_size=1, vad_filter=True, condition_on_previous_text=False,
    )
    out: list[dict] = []
    stop = time.monotonic() + deadline_s
    cut = False
    for seg in segments:
        if time.monotonic() > stop:
            cut = True
            break
        text = _SPACES.sub(" ", str(getattr(seg, "text", "") or "")).strip()
        if not text or float(getattr(seg, "no_speech_prob", 0.0) or 0.0) > NO_SPEECH_PROB:
            continue
        out.append({"start": float(getattr(seg, "start", 0.0) or 0.0),
                    "end": float(getattr(seg, "end", 0.0) or 0.0),
                    "text": text[:SEGMENT_CHARS]})
    return out, cut


def transcribe(video: bytes, suffix: str = ".mp4") -> Heard:
    """Blocking (run it in a thread). Never raises."""
    name = model_name()
    if not name:
        return Heard(OFF)
    if not installed():
        return Heard(NOT_INSTALLED)
    if not _busy.acquire(blocking=False):
        return Heard(FAILED, note="busy")
    try:
        return _transcribe(video, suffix, name)
    finally:
        _busy.release()


def _transcribe(video: bytes, suffix: str, name: str) -> Heard:
    from core.tools_watch import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return Heard(FAILED, note="no ffmpeg")
    folder = tempfile.mkdtemp(prefix="eche-listen-")
    try:
        clip = os.path.join(folder, "clip" + suffix)
        wav = os.path.join(folder, "sound.wav")
        with open(clip, "wb") as handle:
            handle.write(video)
        try:
            if not extract_audio(ffmpeg, clip, wav):
                return Heard(NO_AUDIO)
        except Exception as exc:
            return Heard(FAILED, note=f"extract {type(exc).__name__}")
        try:
            model = get_model(name)
        except Exception as exc:
            return Heard(FAILED, note=f"load {exc}")
        if model is None:
            return Heard(LOADING)
        try:
            segments, cut = run_model(model, wav)
        except Exception as exc:
            return Heard(FAILED, note=type(exc).__name__)
        return Heard(HEARD, segments, cut, "timeout" if cut else "")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def heard_line(heard: Heard, has_audio: bool, stamp) -> str:
    """The "Heard:" part of the watch text, capped at TRANSCRIPT_CHARS."""
    if not has_audio or heard.state == NO_AUDIO:
        return "It has no sound track."
    if heard.state == HEARD:
        if not heard.segments:
            return "Heard: no speech (music, noise or silence)."
        parts: list[str] = []
        used = len("Heard: ")
        for seg in heard.segments:
            piece = f"[{stamp(seg['start'])}] {seg['text']}"
            if used + len(piece) + 2 > TRANSCRIPT_CHARS:
                parts.append("…")
                break
            parts.append(piece)
            used += len(piece) + 2
        line = "Heard: " + "; ".join(parts)
        if heard.cut and parts[-1] != "…":
            line += " … (transcript stopped early)"
        return line
    reason = {
        NOT_INSTALLED: "local speech-to-text isn't installed",
        OFF: "speech-to-text is off",
        LOADING: "the speech-to-text model is still downloading",
    }.get(heard.state, "speech-to-text failed")
    return f"It has a sound track that was not transcribed ({reason})."


def log_line(heard: Heard) -> str:
    """Counts and states only, for the tool detail."""
    bits = [f"audio: {heard.state}"]
    if heard.state == HEARD:
        bits.append(f"segments={len(heard.segments)}")
        bits.append(f"model={_model_name or model_name()}")
    if heard.note:
        bits.append(f"note={heard.note[:40]}")
    return " ".join(bits)
