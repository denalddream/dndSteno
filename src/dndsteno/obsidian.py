"""Turning a session summary into Obsidian changes, and writing them append-only.

Safety rules (enforced here, not by the model):
- existing notes are only ever appended to; their bytes up to the old end stay identical;
- every addition is a marked callout carrying a hidden per-session marker, so it is
  visibly machine-written and never added twice;
- new notes are created exclusively (never overwrite an existing file);
- each note is backed up into the session folder before its first change;
- nothing is written outside the vault.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from dndsteno.config import Config
from dndsteno.session import Session
from dndsteno.vault import VaultIndex

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BAD_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|#^\[\]]')


class UnsafeWrite(Exception):
    pass


@dataclass
class Change:
    id: str
    action: str        # "create" | "append"
    rel_path: str      # relative to the vault root, posix
    title: str         # one line for the list in the UI
    content: str       # whole note (create) or the block to append (append)
    status: str = "pending"  # "pending" | "done" (already in the vault)


def marker(session_name: str) -> str:
    return f"%% dndsteno:session={session_name} %%"


def safe_filename(name: str) -> str:
    name = _BAD_FILENAME_CHARS.sub("", name).strip().lstrip(".")
    return re.sub(r"\s+", " ", name)[:120] or "Без названия"


def _quote(lines: list[str]) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in lines)


def _fact_line(f: dict) -> str:
    time = f" `{f['time']}`" if f.get("time") else ""
    return f"- {f['fact'].strip()}{time}"


def render_append_block(cfg: Config, session_name: str, facts: list[dict]) -> str:
    header = f"[!{cfg.obsidian.callout}] 🤖 Нейросеть · Сессия {session_name}"
    return _quote([header, marker(session_name), *[_fact_line(f) for f in facts]])


def render_new_note(cfg: Config, session_name: str, kind: str, facts: list[dict]) -> str:
    banner = _quote([
        f"[!{cfg.obsidian.callout}] 🤖 Статья создана нейросетью · Сессия {session_name}",
        marker(session_name),
        f"Тип: {kind}. Проверьте и при желании перенесите в нужную папку.",
        "",
        *[_fact_line(f) for f in facts],
    ])
    return banner + "\n"


def render_session_note(cfg: Config, summary: dict) -> str:
    name = summary["session"]
    lines = []
    date = summary.get("in_game_date", "").strip()
    if ISO_DATE.match(date):
        lines += ["---", f"fc-date: {date}", "fc-category: Сессия", "---"]
    lines.append(_quote([
        f"[!{cfg.obsidian.callout}] 🤖 Заметка создана нейросетью · Сессия {name}",
        marker(name),
        f"Собрано автоматически по расшифровке ({summary.get('model', '')}). "
        "Возможны ошибки — сверяйтесь с расшифровкой по времени.",
    ]))
    lines += ["", f"# {summary.get('title') or 'Сессия ' + name}", ""]
    if date:
        lines += [f"Игровая дата: {date}", ""]
    lines += ["## Кратко", *[f"- {t}" for t in summary.get("tldr", [])], ""]
    lines.append("## Ход сессии")
    for s in summary.get("scenes", []):
        start = f" `{s['start']}`" if s.get("start") else ""
        lines += ["", f"### {s.get('title', '')}{start}", s.get("text", "")]
    threads = summary.get("open_threads", [])
    if threads:
        lines += ["", "## Открытые вопросы", *[f"- {t}" for t in threads]]
    entities = summary.get("entities", [])
    if entities:
        lines += ["", "## Упоминаются", " · ".join(f"[[{e['name']}]]" for e in entities)]
    return "\n".join(lines) + "\n"


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def plan_changes(cfg: Config, session: Session, summary: dict, index: VaultIndex) -> list[Change]:
    vault = cfg.vault_path
    campaign = cfg.campaign_path
    if campaign is None or not vault.is_dir():
        raise FileNotFoundError("Не указана папка Obsidian vault (Настройки → Obsidian).")
    name = summary["session"]
    changes: list[Change] = []

    def add(action: str, path: Path, title: str, content: str):
        if not _inside(path, vault):
            raise UnsafeWrite(f"Путь вне vault: {path}")
        rel = path.resolve().relative_to(vault.resolve()).as_posix()
        if path.exists():
            if marker(name) in path.read_text(encoding="utf-8", errors="replace"):
                status = "done"
            else:
                status = "pending"
            if action == "create":
                # Never overwrite: an existing file only gets the content appended as a marked block.
                action = "append"
                if not content.lstrip().startswith(">"):
                    body = re.sub(r"\A---\n.*?\n---\n", "", content, flags=re.DOTALL)  # no frontmatter mid-file
                    content = _quote([f"[!{cfg.obsidian.callout}] 🤖 Нейросеть · Сессия {name}",
                                      marker(name), *body.splitlines()])
        else:
            status = "pending"
        changes.append(Change(f"{action}:{rel}", action, rel, title, content, status))

    add("create", campaign / cfg.obsidian.sessions_dir / f"Сессия {safe_filename(name)}.md",
        f"Заметка о сессии «{summary.get('title', name)}»", render_session_note(cfg, summary))

    for e in summary.get("entities", []):
        if e.get("note"):
            add("append", campaign / e["note"], f"Дополнить «{e['name']}» ({len(e['facts'])} факт.)",
                render_append_block(cfg, name, e["facts"]))
        else:
            existing = index.resolve(e["name"])  # vault may have changed since summarizing
            if existing:
                add("append", existing.path, f"Дополнить «{existing.name}» ({len(e['facts'])} факт.)",
                    render_append_block(cfg, name, e["facts"]))
            else:
                add("create", campaign / cfg.obsidian.new_notes_dir / f"{safe_filename(e['name'])}.md",
                    f"Новая статья «{e['name']}» ({e.get('kind', '')})",
                    render_new_note(cfg, name, e.get("kind", "другое"), e["facts"]))
    return changes


def _atomic_write(path: Path, data: bytes, expect_stat: tuple | None) -> None:
    tmp = path.with_name(path.name + ".dndsteno.tmp")
    tmp.write_bytes(data)
    if expect_stat is not None:
        st = path.stat()
        if (st.st_size, st.st_mtime_ns) != expect_stat:
            tmp.unlink()
            raise UnsafeWrite(f"{path.name} изменился во время записи — повторите.")
    os.replace(tmp, path)


def append_block(path: Path, block: str, session_name: str, backup_path: Path) -> str:
    """Append `block` to an existing note. Returns 'done' or 'skipped'."""
    st = path.stat()
    original = path.read_bytes()
    if marker(session_name).encode("utf-8") in original:
        return "skipped"
    nl = b"\r\n" if b"\r\n" in original else b"\n"
    if not original:
        sep = b""
    elif original.endswith(nl + nl):
        sep = b""
    elif original.endswith(nl):
        sep = nl
    else:
        sep = nl + nl
    addition = sep + block.replace("\n", nl.decode()).encode("utf-8") + nl
    new = original + addition
    if not new.startswith(original):  # cannot happen; the whole point of this module
        raise UnsafeWrite("Проверка целостности не прошла")

    if not backup_path.exists():  # keep the oldest copy: the note before our first change
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(original)

    _atomic_write(path, new, (st.st_size, st.st_mtime_ns))
    if not path.read_bytes().startswith(original):
        raise UnsafeWrite(f"После записи {path.name} не совпадает с исходным текстом! Копия: {backup_path}")
    return "done"


def create_note(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "xb") as f:  # exclusive: fails instead of overwriting
            f.write(content.encode("utf-8"))
    except FileExistsError:
        raise UnsafeWrite(f"{path.name} уже существует — пересоберите список изменений.")
    return "done"


def apply_changes(cfg: Config, session: Session, changes: list[Change]) -> list[tuple[Change, str]]:
    """Apply changes in order. Returns (change, result) with result 'done', 'skipped' or an error text."""
    vault = cfg.vault_path
    results = []
    log = json.loads(session.obsidian_log.read_text(encoding="utf-8")) if session.obsidian_log.exists() else []
    for ch in changes:
        path = vault / ch.rel_path
        try:
            if not _inside(path, vault):
                raise UnsafeWrite(f"Путь вне vault: {path}")
            if ch.action == "create" and not path.exists():
                result = create_note(path, ch.content)
            elif path.exists():
                result = append_block(path, ch.content, session.name,
                                      session.obsidian_backup_dir / ch.rel_path)
            else:
                raise UnsafeWrite(f"{ch.rel_path} не найден — пересоберите список изменений.")
        except (UnsafeWrite, OSError) as e:
            result = f"ошибка: {e}"
        results.append((ch, result))
        if result == "done":
            log.append({"time": datetime.now().isoformat(timespec="seconds"), **asdict(ch), "status": "done"})
    session.obsidian_log.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return results
