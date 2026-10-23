"""Read-only index of the campaign's notes in an Obsidian vault.

Nothing here writes to the vault; see obsidian.py for the (append-only) writer.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

# Names from speech come inflected ("Виктора Кассалантера"); accept close matches.
FUZZY_CUTOFF = 0.84
# Folders whose notes are the players' own characters (used as context for the model).
PLAYER_FOLDER_HINTS = ("приключенц", "игрок", "персонажи игроков", "партия")


def normalize(name: str) -> str:
    name = name.lower().replace("ё", "е").lstrip("!").strip()
    name = re.sub(r"[«»\"'’]", "", name)
    return re.sub(r"\s+", " ", name)


def _aliases_from_frontmatter(text: str) -> list[str]:
    m = re.match(r"---\r?\n(.*?)\r?\n---", text, re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    inline = re.search(r"^aliases:\s*\[(.*?)\]\s*$", block, re.MULTILINE)
    if inline:
        return [a.strip().strip("\"'") for a in inline.group(1).split(",") if a.strip()]
    listed = re.search(r"^aliases:\s*\r?\n((?:\s*-\s*.+\r?\n?)+)", block, re.MULTILINE)
    if listed:
        return [line.strip()[1:].strip().strip("\"'") for line in listed.group(1).splitlines() if line.strip()]
    return []


@dataclass
class Note:
    path: Path      # absolute
    rel: Path       # relative to the campaign folder
    aliases: list[str]

    @property
    def name(self) -> str:
        """Exact link target: [[name]]."""
        return self.path.stem

    @property
    def folder(self) -> str:
        return self.rel.parent.as_posix() if self.rel.parent != Path(".") else ""

    @property
    def is_player_character(self) -> bool:
        folder = self.folder.lower()
        return any(h in folder for h in PLAYER_FOLDER_HINTS)

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8", errors="replace")


class VaultIndex:
    def __init__(self, campaign_dir: Path, exclude: list[Path] | None = None):
        self.root = campaign_dir
        self.notes: list[Note] = []
        excluded = [e.resolve() for e in (exclude or [])]
        if campaign_dir.exists():
            for path in sorted(campaign_dir.rglob("*.md")):
                if any(part.startswith(".") for part in path.relative_to(campaign_dir).parts):
                    continue
                if any(path.resolve().is_relative_to(e) for e in excluded):
                    continue
                try:
                    head = path.read_text(encoding="utf-8", errors="replace")[:2000]
                except OSError:
                    head = ""
                aliases = _aliases_from_frontmatter(head)
                # "Бадб + Эйлет" is one note for two names.
                if " + " in path.stem:
                    aliases += [p.strip() for p in path.stem.split(" + ")]
                self.notes.append(Note(path, path.relative_to(campaign_dir), aliases))

        self._by_key: dict[str, Note] = {}
        for note in self.notes:
            for key in [note.name, *note.aliases]:
                self._by_key.setdefault(normalize(key), note)

    @property
    def names(self) -> list[str]:
        return [n.name for n in self.notes]

    def get(self, name: str) -> Note | None:
        return next((n for n in self.notes if n.name == name), None)

    def resolve(self, name: str) -> Note | None:
        """Note for a name as said at the table: exact, alias, or a close (inflected) match."""
        key = normalize(name.strip("[] "))
        if not key:
            return None
        if key in self._by_key:
            return self._by_key[key]
        close = difflib.get_close_matches(key, self._by_key.keys(), n=2, cutoff=FUZZY_CUTOFF)
        if not close:
            return None
        # Ambiguous between two different notes ("красный глаз" vs "зеленый глаз"): don't guess.
        if len(close) == 2 and self._by_key[close[0]] is not self._by_key[close[1]]:
            a = difflib.SequenceMatcher(None, key, close[0]).ratio()
            b = difflib.SequenceMatcher(None, key, close[1]).ratio()
            if a - b < 0.05:
                return None
        return self._by_key[close[0]]

    def catalog(self) -> str:
        """Compact list of notes for the model's context, grouped by folder."""
        groups: dict[str, list[str]] = {}
        for note in self.notes:
            groups.setdefault(note.folder or "(корень)", []).append(note.name)
        return "\n".join(f"{folder}: {', '.join(names)}" for folder, names in groups.items())
