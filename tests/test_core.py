import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from dndsteno.cli import _level_bar
from dndsteno.config import TranscribeConfig, load_config
from dndsteno.recorder import ChunkedWriter
from dndsteno.session import Session, latest_session
from dndsteno.transcriber import (
    Segment, Transcriber, build_prompts, group_paragraphs, is_hallucination, normalize, render_markdown,
)


def test_config_defaults_and_overrides(tmp_path: Path):
    (tmp_path / "glossary.txt").write_text("Гримтоф\n# comment\n\nЭльдрин\n", encoding="utf-8")
    path = tmp_path / "dndsteno.toml"
    path.write_text(
        'sessions_dir = "data"\n'
        '[record]\nchunk_minutes = 5\n'
        '[transcribe]\nmodel = "small"\nglossary = ["Эльдрин", "Барак"]\nglossary_file = "glossary.txt"\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.sessions_dir == tmp_path / "data"
    assert cfg.record.chunk_minutes == 5
    assert cfg.record.samplerate == 16000
    assert cfg.transcribe.model == "small"
    assert cfg.transcribe.load_glossary(cfg.base_dir) == ["Эльдрин", "Барак", "Гримтоф"]


def test_config_rejects_typos(tmp_path: Path):
    path = tmp_path / "dndsteno.toml"
    path.write_text('[transcribe]\nlangauge = "ru"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="langauge"):
        load_config(path)


def test_part_numbering_continues(tmp_path: Path):
    s = Session(tmp_path / "2026-10-23")
    assert s.next_part_path().name == "part_001.flac"
    s.audio_dir.mkdir(parents=True)
    (s.audio_dir / "part_001.flac").touch()
    (s.audio_dir / "part_007.flac").touch()
    assert s.next_part_path().name == "part_008.flac"


def test_latest_session_ignores_empty(tmp_path: Path):
    (tmp_path / "empty").mkdir()
    s = Session(tmp_path / "real")
    s.audio_dir.mkdir(parents=True)
    (s.audio_dir / "part_001.flac").touch()
    assert latest_session(tmp_path) == s


def test_chunked_writer_rotates(tmp_path: Path):
    s = Session(tmp_path / "s")
    w = ChunkedWriter(s, samplerate=16000, chunk_frames=1000)
    for _ in range(5):
        w.write(np.full(450, 0.1, dtype=np.float32))
    w.close()
    assert [f.name for f in w.files] == ["part_001.flac", "part_002.flac", "part_003.flac"]
    assert [sf.info(f).frames for f in w.files] == [1000, 1000, 250]
    assert w.total_frames == 2250


def test_build_prompts_caps_hotwords():
    initial, hot = build_prompts([])
    assert hot is None and "Dungeons" in initial
    _, hot = build_prompts([f"Имя{i}" for i in range(500)])
    assert len(hot) <= 600 and hot.startswith("Имя0, Имя1")


def test_paragraphs_and_markdown():
    segs = [Segment(0, 2, "Привет."), Segment(2.5, 4, "Кидаю инициативу."),
            Segment(10, 12, "Семнадцать."), Segment(3725, 3727, "Конец.")]
    assert [len(p) for p in group_paragraphs(segs)] == [2, 1, 1]
    md = render_markdown(segs, "Сессия X")
    assert md.startswith("# Сессия X")
    assert "`00:00:00` Привет. Кидаю инициативу." in md
    assert "`01:02:05` Конец." in md


def test_normalize_boosts_quiet_audio_with_capped_gain():
    quiet = np.full(10000, 0.02, dtype=np.float32)
    assert np.isclose(normalize(quiet).max(), 0.5)
    near_silence = np.full(10000, 1e-5, dtype=np.float32)
    assert np.isclose(normalize(near_silence).max(), 3e-4)  # gain capped at 30
    clicky = np.full(10000, 0.02, dtype=np.float32)
    clicky[0] = 1.0  # one loud click must not suppress the gain
    assert np.isclose(np.median(normalize(clicky)), 0.5, atol=0.05)
    assert normalize(np.zeros(5, dtype=np.float32)).max() == 0


def test_hallucination_filter():
    assert is_hallucination("Продолжение следует...")
    assert is_hallucination("Субтитры сделал DimaTorzok")
    assert not is_hallucination("Гримтоф кидает на атлетику, семнадцать.")


def test_level_bar_db_scale():
    assert _level_bar(1.0).endswith("0 дБ") and "·" not in _level_bar(1.0)
    assert "-40 дБ" in _level_bar(0.01)
    assert "█" not in _level_bar(0.0)


class FakeTranscriber(Transcriber):
    def __init__(self):
        super().__init__(TranscribeConfig(model="fake"))
        self.calls = []

    def transcribe_file(self, path, on_position=None):
        self.calls.append(path.name)
        return [Segment(1.0, 2.0, f"текст {path.stem}")], 100.0


def test_transcribe_parts_offsets_and_cache(tmp_path: Path):
    files = []
    for name in ("part_001.flac", "part_002.flac"):
        f = tmp_path / name
        f.write_bytes(b"x")
        files.append(f)

    t = FakeTranscriber()
    segs = t.transcribe_parts(files, tmp_path / "cache")
    assert [(s.start, s.text) for s in segs] == [(1.0, "текст part_001"), (101.0, "текст part_002")]
    assert t.calls == ["part_001.flac", "part_002.flac"]

    # Second run hits the cache; a changed file is redone.
    files[1].write_bytes(b"xy")
    t2 = FakeTranscriber()
    t2.transcribe_parts(files, tmp_path / "cache")
    assert t2.calls == ["part_002.flac"]
    assert json.loads((tmp_path / "cache" / "part_001.json").read_text("utf-8"))["duration"] == 100.0
