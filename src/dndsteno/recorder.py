"""Microphone recording straight to disk, split into FLAC parts.

Audio never accumulates in memory: the stream callback hands blocks to a queue
and the main loop writes them out immediately. A new part file starts every
`chunk_minutes`, so a crash loses at most the part being written.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from dndsteno.config import RecordConfig
from dndsteno.session import Session

CHANNELS = 1


class ChunkedWriter:
    """Writes mono float32 blocks into session parts, rotating every `chunk_frames` frames."""

    def __init__(self, session: Session, samplerate: int, chunk_frames: int):
        self.session = session
        self.samplerate = samplerate
        self.chunk_frames = chunk_frames
        self.files: list[Path] = []
        self.total_frames = 0
        self._file: sf.SoundFile | None = None
        self._frames_in_file = 0

    def _open_next(self) -> None:
        self.session.audio_dir.mkdir(parents=True, exist_ok=True)
        path = self.session.next_part_path(".flac")
        self._file = sf.SoundFile(
            path, mode="w", samplerate=self.samplerate, channels=CHANNELS,
            format="FLAC", subtype="PCM_16",
        )
        self._frames_in_file = 0
        self.files.append(path)

    def write(self, block: np.ndarray) -> None:
        while len(block):
            if self._file is None or self._frames_in_file >= self.chunk_frames:
                self.close()
                self._open_next()
            room = self.chunk_frames - self._frames_in_file
            piece, block = block[:room], block[room:]
            self._file.write(piece)
            self._frames_in_file += len(piece)
            self.total_frames += len(piece)

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


@dataclass
class RecordStatus:
    seconds: float
    peak: float          # 0..1, loudest sample over the last report interval
    current_file: Path | None
    overflows: int


@dataclass
class RecordResult:
    files: list[Path] = field(default_factory=list)
    seconds: float = 0.0
    samplerate: int = 0
    overflows: int = 0


@dataclass
class InputDevice:
    index: int
    name: str
    hostapi: str
    samplerate: int
    is_default: bool

    @property
    def key(self) -> str:
        """Stable id for the config: indices shift when e.g. Bluetooth headphones connect."""
        return f"{self.name} [{self.hostapi}]"


def list_input_devices() -> list[InputDevice]:
    import sounddevice as sd

    default_in = sd.default.device[0]
    return [
        InputDevice(
            index=i,
            name=" ".join(dev["name"].split()),  # some drivers put newlines into names
            hostapi=sd.query_hostapis(dev["hostapi"])["name"],
            samplerate=int(dev["default_samplerate"]),
            is_default=i == default_in,
        )
        for i, dev in enumerate(sd.query_devices())
        if dev["max_input_channels"] > 0
    ]


def resolve_device(device: str) -> int | str | None:
    """Config value -> sounddevice argument. Accepts an index, an InputDevice.key, or a name substring."""
    device = device.strip()
    if not device:
        return None
    if device.isdigit():
        return int(device)
    for d in list_input_devices():
        if d.key == device:
            return d.index
    return device


def pick_samplerate(device: int | str | None, wanted: int) -> int:
    """`wanted` if the device accepts it, otherwise the device's native rate."""
    import sounddevice as sd

    try:
        sd.check_input_settings(device=device, samplerate=wanted, channels=CHANNELS)
        return wanted
    except sd.PortAudioError:
        return int(sd.query_devices(device, "input")["default_samplerate"])


def record(
    session: Session,
    cfg: RecordConfig,
    stop: threading.Event,
    on_status: Callable[[RecordStatus], None] | None = None,
    status_interval: float = 0.5,
) -> RecordResult:
    """Record until `stop` is set (or KeyboardInterrupt). Always closes files cleanly."""
    import sounddevice as sd

    device = resolve_device(cfg.device)
    samplerate = pick_samplerate(device, cfg.samplerate)
    blocks: queue.Queue[np.ndarray] = queue.Queue()
    overflows = 0

    def callback(indata, frames, time_info, status):
        nonlocal overflows
        if status.input_overflow:
            overflows += 1
        blocks.put(indata[:, 0].copy())

    writer = ChunkedWriter(session, samplerate, int(cfg.chunk_minutes * 60 * samplerate))
    peak = 0.0
    last_report = time.monotonic()

    def drain(timeout: float | None) -> None:
        nonlocal peak
        block = blocks.get(timeout=timeout) if timeout else blocks.get_nowait()
        writer.write(block)
        peak = max(peak, float(np.abs(block).max(initial=0.0)))

    try:
        with sd.InputStream(device=device, samplerate=samplerate, channels=CHANNELS,
                            dtype="float32", callback=callback):
            while not stop.is_set():
                try:
                    drain(timeout=0.2)
                except queue.Empty:
                    pass
                now = time.monotonic()
                if on_status and now - last_report >= status_interval:
                    on_status(RecordStatus(
                        seconds=writer.total_frames / samplerate,
                        peak=peak,
                        current_file=writer.files[-1] if writer.files else None,
                        overflows=overflows,
                    ))
                    peak = 0.0
                    last_report = now
    except KeyboardInterrupt:
        pass
    finally:
        # Stream is closed here; flush whatever the callback already queued.
        while True:
            try:
                drain(timeout=None)
            except queue.Empty:
                break
        writer.close()

    return RecordResult(
        files=writer.files,
        seconds=writer.total_frames / samplerate,
        samplerate=samplerate,
        overflows=overflows,
    )
