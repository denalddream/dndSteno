"""Append-only guarantees for writing into the vault, and the summary pipeline with a fake model."""

import json
from pathlib import Path

import pytest

from dndsteno.config import Config
from dndsteno.obsidian import (
    UnsafeWrite, _atomic_write, append_block, apply_changes, marker, plan_changes, safe_filename,
)
from dndsteno.session import Session
from dndsteno.summarize import chunk_transcript, summarize_session
from dndsteno.transcriber import Segment, write_outputs
from dndsteno.vault import VaultIndex

VICTOR = (
    "---\r\nДата рождения: 1305-04-23\r\nfc-category: ДР\r\n---\r\n"
    "- Хозяин дома [[Кассалантеры]]\r\n- Хранит [[!!!Камень Голорра]]"  # CRLF, no trailing newline
)


@pytest.fixture
def world(tmp_path: Path):
    vault = tmp_path / "vault"
    camp = vault / "Большой куш"
    (camp / "Персонажи" / "Важные персонажи").mkdir(parents=True)
    (camp / "Персонажи" / "Приключенцы").mkdir(parents=True)
    (camp / "Места").mkdir()
    (camp / "Персонажи" / "Важные персонажи" / "Виктор Кассалантер.md").write_bytes(VICTOR.encode("utf-8"))
    (camp / "Персонажи" / "Приключенцы" / "Бадб + Эйлет.md").write_text("# Бадб\n- воин\n", encoding="utf-8")
    (camp / "Места" / "!Вотердип.md").write_text("", encoding="utf-8")

    cfg = Config(path=tmp_path / "dndsteno.toml")
    cfg.obsidian.vault_dir = str(vault)
    cfg.obsidian.campaign_dir = "Большой куш"
    session = Session(tmp_path / "sessions" / "2026-10-23")
    session.root.mkdir(parents=True)
    return cfg, session, camp


def summary_for(session: Session, entities: list[dict]) -> dict:
    return {"session": session.name, "model": "fake", "in_game_date": "1452-11-12",
            "title": "Ужин у Кассалантеров", "tldr": ["Партия ужинала"], "open_threads": ["Кто отравил вино?"],
            "scenes": [{"title": "Ужин", "text": "Был ужин.", "start": "00:10:00"}], "entities": entities}


def test_append_keeps_original_bytes_and_marks_block(world):
    cfg, session, camp = world
    victor = camp / "Персонажи" / "Важные персонажи" / "Виктор Кассалантер.md"
    original = victor.read_bytes()
    summary = summary_for(session, [{"name": "Виктор Кассалантер",
                                     "note": "Персонажи/Важные персонажи/Виктор Кассалантер.md",
                                     "kind": "персонаж", "facts": [{"fact": "Пригласил [[Бадб + Эйлет]] на ужин",
                                                                    "time": "01:12:05"}]}])
    changes = plan_changes(cfg, session, summary, VaultIndex(camp))
    results = apply_changes(cfg, session, changes)
    assert all(r == "done" for _, r in results), results

    new = victor.read_bytes()
    assert new.startswith(original)                          # nothing removed or altered
    added = new[len(original):].decode("utf-8")
    assert "\r\n" in added and "\n" not in added.replace("\r\n", "")  # keeps the file's CRLF style
    assert "[!ai] 🤖 Нейросеть · Сессия 2026-10-23" in added
    assert marker("2026-10-23") in added
    assert "Пригласил [[Бадб + Эйлет]] на ужин `01:12:05`" in added
    assert (session.obsidian_backup_dir / "Большой куш/Персонажи/Важные персонажи/Виктор Кассалантер.md"
            ).read_bytes() == original

    # Running again changes nothing.
    again = plan_changes(cfg, session, summary, VaultIndex(camp))
    assert all(c.status == "done" for c in again)
    assert victor.read_bytes() == new
    assert append_block(victor, "> x", session.name, tmp_backup := session.root / "b.md") == "skipped"
    assert not tmp_backup.exists()


def test_session_note_and_new_entity_created_never_overwritten(world):
    cfg, session, camp = world
    summary = summary_for(session, [{"name": "Гримтоф", "note": None, "kind": "персонаж",
                                     "facts": [{"fact": "Гном-кузнец из Доков", "time": "00:20:00"}]}])
    changes = plan_changes(cfg, session, summary, VaultIndex(camp))
    assert [c.action for c in changes] == ["create", "create"]
    apply_changes(cfg, session, changes)

    note = (camp / "Сессии" / "Сессия 2026-10-23.md").read_text(encoding="utf-8")
    assert note.startswith("---\nfc-date: 1452-11-12\nfc-category: Сессия\n---\n> [!ai]")
    assert "# Ужин у Кассалантеров" in note and "## Открытые вопросы" in note and "[[Гримтоф]]" in note
    new_entity = (camp / "Новое от нейросети" / "Гримтоф.md").read_text(encoding="utf-8")
    assert "Статья создана нейросетью" in new_entity and "Гном-кузнец из Доков" in new_entity

    # A note that appeared meanwhile is never overwritten.
    (camp / "Сессии" / "Сессия 2026-10-23.md").write_text("моя заметка", encoding="utf-8")
    stale_create = [c for c in changes if "Сессии" in c.rel_path]
    (result,) = [r for _, r in apply_changes(cfg, session, stale_create)]
    # The existing file has no marker -> appended, not replaced.
    text = (camp / "Сессии" / "Сессия 2026-10-23.md").read_text(encoding="utf-8")
    assert text.startswith("моя заметка") and result == "done"


def test_existing_file_without_marker_gets_append_not_create(world):
    cfg, session, camp = world
    (camp / "Сессии").mkdir()
    (camp / "Сессии" / "Сессия 2026-10-23.md").write_text("---\nfc-date: 1\n---\nсвоё\n", encoding="utf-8")
    changes = plan_changes(cfg, session, summary_for(session, []), VaultIndex(camp))
    assert changes[0].action == "append"
    assert changes[0].content.startswith("> [!ai]") and "fc-date: 1452" not in changes[0].content


def test_paths_cannot_escape_vault(world):
    cfg, session, camp = world
    assert safe_filename("../../Windows/evil") == "Windowsevil"
    summary = summary_for(session, [{"name": "../../../evil", "note": None, "kind": "другое",
                                     "facts": [{"fact": "x", "time": ""}]}])
    changes = plan_changes(cfg, session, summary, VaultIndex(camp))
    for c in changes:
        assert ".." not in c.rel_path


def test_concurrent_edit_aborts_write(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("до", encoding="utf-8")
    st = f.stat()
    f.write_text("пользователь успел изменить", encoding="utf-8")
    with pytest.raises(UnsafeWrite):
        _atomic_write(f, b"new", (st.st_size, st.st_mtime_ns))
    assert f.read_text(encoding="utf-8") == "пользователь успел изменить"
    assert not list(tmp_path.glob("*.tmp"))


def test_vault_resolve_handles_cases_and_ambiguity(world):
    _, _, camp = world
    index = VaultIndex(camp)
    assert index.resolve("Виктора Кассалантера").name == "Виктор Кассалантер"
    assert index.resolve("Эйлет").name == "Бадб + Эйлет"
    assert index.resolve("Вотердипе").name == "!Вотердип"
    assert index.resolve("Гримтоф") is None


class FakeLLM:
    """Plays the model: map returns facts, reduce a summary, dedupe drops facts already in the note."""

    model = "fake-model"

    def __init__(self):
        self.calls = []

    def chat_json(self, system, user, schema, on_text=None, retries=1):
        self.calls.append(system[:20])
        if "keep" in schema["properties"]:
            note, candidates = user.split("Сведения из сессии:\n")
            numbered = [line.split(". ", 1) for line in candidates.splitlines()]
            # Also try to smuggle in an index that doesn't exist; the code must ignore it.
            return {"keep": [int(n) for n, fact in numbered if fact not in note] + [99]}
        if "tldr" in schema["properties"]:
            return {"title": "Тест", "tldr": ["Был [[Гримтоф]] и [[Неизвестный]]"],
                    "scenes": [{"title": "Сцена", "text": "Текст про [[Виктор Кассалантер]]", "start": "00:00:01"}],
                    "open_threads": []}
        return {"scenes": [{"title": "Сцена", "summary": "s", "start": "00:00:01"}],
                "facts": [{"entity": "Виктора Кассалантера", "kind": "персонаж",
                           "fact": "- Хозяин дома [[Кассалантеры]]", "time": "00:00:01"},
                          {"entity": "Виктора Кассалантера", "kind": "персонаж",
                           "fact": "Пригласил партию на ужин", "time": "00:00:02"},
                          {"entity": "Гримтоф", "kind": "персонаж", "fact": "Кузнец", "time": "00:00:03"},
                          # Not mentioned in the transcript -> must be dropped.
                          {"entity": "Ацерерак", "kind": "персонаж", "fact": "Убит в лесу", "time": "00:00:04"}],
                "open_threads": [], "in_game_date": ""}


def test_summarize_with_fake_model(world):
    cfg, session, camp = world
    segments = [Segment(i * 3.0, i * 3.0 + 2, f"Виктор Кассалантер и Гримтоф, реплика {i} " * 3)
                for i in range(40)]
    write_outputs(segments, session.transcript_json, session.transcript_md, "t")
    cfg.llm.chunk_chars = 1500
    assert len(chunk_transcript(segments, 1500)) > 1

    llm = FakeLLM()
    summary = summarize_session(cfg, session, llm, VaultIndex(camp))
    by_name = {e["name"]: e for e in summary["entities"]}
    victor = by_name["Виктор Кассалантер"]
    assert victor["note"] == "Персонажи/Важные персонажи/Виктор Кассалантер.md"
    # Fact already in the note dropped, repeats across chunks merged, invented index ignored.
    assert [f["fact"] for f in victor["facts"]] == ["Пригласил партию на ужин"]
    assert by_name["Гримтоф"]["facts"][0]["time"] == "00:00:03"
    assert by_name["Гримтоф"]["note"] is None
    assert "Ацерерак" not in by_name
    assert summary["tldr"] == ["Был [[Гримтоф]] и Неизвестный"]  # unknown link unwrapped
    assert json.loads(session.summary_json.read_text(encoding="utf-8"))["title"] == "Тест"

    # Map results are cached per chunk: the second run only repeats reduce + dedupe.
    llm2 = FakeLLM()
    summarize_session(cfg, session, llm2, VaultIndex(camp))
    assert len(llm2.calls) < len(llm.calls)
