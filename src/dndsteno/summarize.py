"""Session summary and per-note facts from a transcript, via a local LLM.

1. map:    each transcript chunk -> scenes, facts about entities, open threads (cached per chunk)
2. reduce: all scenes -> session title, short summary, polished scene list
3. dedupe: for each existing note that got facts -> keep only facts the note doesn't already state

Output is summary.json in the session folder; obsidian.py turns it into note changes.
The model never sees or writes files: it only returns JSON.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from dndsteno.config import Config
from dndsteno.llm import LLMClient
from dndsteno.session import Session
from dndsteno.transcriber import Segment, format_timestamp, group_paragraphs
from dndsteno.vault import VaultIndex

PROMPT_VERSION = 1
NOTE_CONTEXT_CHARS = 12000  # how much of an existing note the dedupe step shows the model

ENTITY_KINDS = ["персонаж", "место", "предмет", "фракция", "событие", "другое"]

_COMMON_RULES = """\
Правила:
- Это живая игра за столом, записанная одним микрофоном. Расшифровка автоматическая: в ней есть ошибки \
распознавания, реплики не подписаны, мастер и игроки говорят вперемешку.
- Игнорируй разговоры не по игре: еду, шутки, обсуждение правил и бросков, если они не влияют на сюжет.
- Пиши только то, что действительно прозвучало. Ничего не выдумывай и не додумывай.
- Пиши по-русски, коротко, в стиле заметок мастера: факт — одно предложение.
- Если имя похоже на статью из списка ниже — используй точное название статьи из списка.
- Время указывай в формате ЧЧ:ММ:СС по меткам из расшифровки."""


def _known_block(index: VaultIndex | None, glossary: list[str]) -> str:
    parts = []
    if index and index.notes:
        players = [n.name for n in index.notes if n.is_player_character]
        if players:
            parts.append("Персонажи игроков: " + ", ".join(players))
        parts.append("Существующие статьи кампании (папка: названия):\n" + index.catalog())
    if glossary:
        parts.append("Словарь имён: " + ", ".join(glossary))
    return "\n\n".join(parts) or "Статей кампании пока нет."


MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "summary": {"type": "string"},
                           "start": {"type": "string"}},
            "required": ["title", "summary", "start"], "additionalProperties": False}},
        "facts": {"type": "array", "items": {
            "type": "object",
            "properties": {"entity": {"type": "string"}, "kind": {"type": "string", "enum": ENTITY_KINDS},
                           "fact": {"type": "string"}, "time": {"type": "string"}},
            "required": ["entity", "kind", "fact", "time"], "additionalProperties": False}},
        "open_threads": {"type": "array", "items": {"type": "string"}},
        "in_game_date": {"type": "string"},
    },
    "required": ["scenes", "facts", "open_threads", "in_game_date"],
    "additionalProperties": False,
}

MAP_SYSTEM = """\
Ты — летописец кампании Dungeons & Dragons «{campaign}». Тебе дают кусок расшифровки сессии.
Верни JSON:
- scenes: сцены этого куска по порядку (title — короткое название, summary — 2–5 предложений \
о том, что произошло, start — время начала);
- facts: новые сведения о конкретных персонажах, местах, предметах, фракциях — что о них узнали \
или что с ними произошло. entity — точное название статьи из списка, если подходит, иначе имя как \
прозвучало в именительном падеже. Не включай очевидное и мелочи;
- open_threads: незакрытые зацепки, задания, вопросы, к которым стоит вернуться;
- in_game_date: внутриигровая дата, если она прямо прозвучала, иначе пустая строка.

{rules}

{known}"""

REDUCE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "tldr": {"type": "array", "items": {"type": "string"}},
        "scenes": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "text": {"type": "string"},
                           "start": {"type": "string"}},
            "required": ["title", "text", "start"], "additionalProperties": False}},
        "open_threads": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "tldr", "scenes", "open_threads"],
    "additionalProperties": False,
}

REDUCE_SYSTEM = """\
Ты — летописец кампании Dungeons & Dragons «{campaign}». Тебе дают черновые сцены всей сессии \
по порядку (их писали по кускам, поэтому на стыках бывают повторы и обрывы) и черновой список \
незакрытых зацепок. Собери итог:
- title: название сессии в 2–6 слов;
- tldr: 5–10 пунктов — самое главное, что произошло;
- scenes: сцены без повторов, соседние куски одной сцены объедини; text — связный пересказ \
в 2–6 предложений; start — время начала;
- open_threads: зацепки без повторов и без уже закрытых по ходу сессии.
Используй [[точные названия]] статей из списка, когда упоминаешь их.

{rules}

{known}"""

# The dedupe step only *selects* candidate numbers; fact text always comes from the map step,
# which saw the transcript. A model asked to rewrite facts here invented biographies.
DEDUPE_SCHEMA = {
    "type": "object",
    "properties": {"keep": {"type": "array", "items": {"type": "integer"}}},
    "required": ["keep"],
    "additionalProperties": False,
}

DEDUPE_SYSTEM = """\
Ниже — текст статьи Obsidian «{name}» и пронумерованный список сведений из новой игровой сессии.
Верни в keep номера тех сведений, которых в статье ещё НЕТ (сравнивай по смыслу).
Если сведение уже есть в статье или повторяет другое из списка — его номер не включай.
Ничего не пиши, кроме номеров."""


@dataclass
class SummaryProgress:
    stage: str          # "map" | "reduce" | "dedupe"
    index: int          # 1-based within the stage
    count: int
    chars: int = 0      # characters generated so far for the current request
    cached: bool = False


ProgressCallback = Callable[[SummaryProgress], None]


def load_segments(session: Session) -> list[Segment]:
    data = json.loads(session.transcript_json.read_text(encoding="utf-8"))
    return [Segment(**s) for s in data]


def chunk_transcript(segments: list[Segment], max_chars: int) -> list[str]:
    """Paragraph lines '[hh:mm:ss] text' packed into chunks of at most ~max_chars."""
    chunks, current, size = [], [], 0
    for para in group_paragraphs(segments):
        line = f"[{format_timestamp(para[0].start)}] " + " ".join(s.text for s in para)
        if current and size + len(line) > max_chars:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def _cache_key(*parts: str) -> str:
    return hashlib.sha256("\x00".join([str(PROMPT_VERSION), *parts]).encode("utf-8")).hexdigest()[:16]


def _stem(word: str) -> str:
    """Crude Russian stem: enough to match 'Кассалантером' with 'Кассалантер'."""
    word = word.lower().replace("ё", "е")
    return word[:max(4, len(word) - 2)] if len(word) > 5 else word


def mentioned_in(entity: str, text: str) -> bool:
    """True if some significant word of `entity` (inflection-tolerant) occurs in `text`."""
    haystack = text.lower().replace("ё", "е")
    words = [w for w in re.findall(r"[\w'’-]+", entity.lstrip("!")) if len(w) >= 4]
    if not words:
        return entity.lower() in haystack
    return any(_stem(w) in haystack for w in words)


def first_mention_time(entity: str, chunk: str) -> str:
    """Timestamp of the first transcript line in `chunk` that mentions `entity`, or ''."""
    for line in chunk.splitlines():
        m = re.match(r"\[(\d{2}:\d{2}:\d{2})\]", line)
        if m and mentioned_in(entity, line):
            return m.group(1)
    return ""


def _unique_facts(facts: list[dict]) -> list[dict]:
    seen, out = set(), []
    for f in facts:
        key = re.sub(r"\W+", " ", f["fact"].lower()).strip()
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _strip_unknown_links(text: str, allowed: set[str]) -> str:
    """[[X]] -> X unless X is an existing note or a note this run will create."""
    def repl(m):
        target = m.group(1).split("|")[0].strip()
        return m.group(0) if target in allowed else m.group(1).split("|")[-1]
    return re.sub(r"\[\[([^\]]+)\]\]", repl, text)


def summarize_session(
    cfg: Config,
    session: Session,
    client: LLMClient,
    index: VaultIndex | None,
    force: bool = False,
    on_progress: ProgressCallback | None = None,
) -> dict:
    campaign = cfg.obsidian.campaign_dir or "кампания"
    glossary = cfg.transcribe.load_glossary(cfg.base_dir)
    known = _known_block(index, glossary)
    rules = _COMMON_RULES

    def report(p: SummaryProgress):
        if on_progress:
            on_progress(p)

    # --- 1. map ---
    chunks = chunk_transcript(load_segments(session), cfg.llm.chunk_chars)
    session.summary_parts_dir.mkdir(parents=True, exist_ok=True)
    map_system = MAP_SYSTEM.format(campaign=campaign, rules=rules, known=known)
    partials = []
    for i, chunk in enumerate(chunks, 1):
        key = _cache_key(client.model, map_system, chunk)
        cache = session.summary_parts_dir / f"chunk_{i:03d}.json"
        if not force and cache.exists():
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("key") == key:
                partials.append(cached["result"])
                report(SummaryProgress("map", i, len(chunks), cached=True))
                continue
        result = client.chat_json(
            map_system, f"Кусок {i} из {len(chunks)}:\n\n{chunk}", MAP_SCHEMA,
            on_text=lambda n, i=i: report(SummaryProgress("map", i, len(chunks), chars=n)),
        )
        cache.write_text(json.dumps({"key": key, "result": result}, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        partials.append(result)

    # --- 2. reduce ---
    draft_scenes = [s for p in partials for s in p.get("scenes", [])]
    draft_threads = [t for p in partials for t in p.get("open_threads", [])]
    reduce_input = "Сцены:\n" + "\n".join(
        f"- [{s.get('start', '')}] {s.get('title', '')}: {s.get('summary', '')}" for s in draft_scenes
    ) + "\n\nЗацепки:\n" + "\n".join(f"- {t}" for t in draft_threads)
    report(SummaryProgress("reduce", 1, 1))
    final = client.chat_json(
        REDUCE_SYSTEM.format(campaign=campaign, rules=rules, known=known), reduce_input, REDUCE_SCHEMA,
        on_text=lambda n: report(SummaryProgress("reduce", 1, 1, chars=n)),
    )

    # --- 3. group facts by note; dedupe against existing notes ---
    groups: dict[str, dict] = {}
    for chunk, p in zip(chunks, partials):
        for f in p.get("facts", []):
            entity = f.get("entity", "").strip().strip("[]")
            if not entity or not f.get("fact"):
                continue
            if not mentioned_in(entity, chunk):
                continue  # the model attributed a fact to someone who isn't in this part of the transcript
            if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", f.get("time", "")):
                f["time"] = first_mention_time(entity, chunk)
            note = index.resolve(entity) if index else None
            key = note.name if note else entity
            group = groups.setdefault(key, {"name": key, "note": note.rel.as_posix() if note else None,
                                            "kind": f.get("kind", "другое"), "facts": []})
            group["facts"].append({"fact": f["fact"], "time": f.get("time", "")})

    for group in groups.values():
        group["facts"] = _unique_facts(group["facts"])
    known_groups = [g for g in groups.values() if g["note"]]
    for i, group in enumerate(known_groups, 1):
        note = index.get(group["name"])
        text = note.read()
        if len(text) > NOTE_CONTEXT_CHARS:
            text = text[:NOTE_CONTEXT_CHARS // 2] + "\n…\n" + text[-NOTE_CONTEXT_CHARS // 2:]
        candidates = "\n".join(f"{n}. {f['fact']}" for n, f in enumerate(group["facts"], 1))
        report(SummaryProgress("dedupe", i, len(known_groups)))
        result = client.chat_json(
            DEDUPE_SYSTEM.format(name=group["name"]),
            f"Статья «{group['name']}»:\n\n{text}\n\nСведения из сессии:\n{candidates}",
            DEDUPE_SCHEMA,
            on_text=lambda n, i=i: report(SummaryProgress("dedupe", i, len(known_groups), chars=n)),
        )
        keep = {k for k in result.get("keep", []) if isinstance(k, int)}
        group["facts"] = [f for n, f in enumerate(group["facts"], 1) if n in keep]

    entities = [g for g in groups.values() if g["facts"]]
    allowed_links = set(index.names if index else []) | {g["name"] for g in entities}
    for g in entities:
        for f in g["facts"]:
            f["fact"] = _strip_unknown_links(f["fact"], allowed_links)
    final["tldr"] = [_strip_unknown_links(t, allowed_links) for t in final.get("tldr", [])]
    for s in final.get("scenes", []):
        s["text"] = _strip_unknown_links(s.get("text", ""), allowed_links)

    in_game_date = next((p["in_game_date"] for p in partials if p.get("in_game_date")), "")
    summary = {
        "session": session.name,
        "created": datetime.now().isoformat(timespec="seconds"),
        "model": client.model,
        "in_game_date": in_game_date,
        **final,
        "entities": entities,
    }
    session.summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def load_summary(session: Session) -> dict | None:
    if not session.summary_json.exists():
        return None
    return json.loads(session.summary_json.read_text(encoding="utf-8"))
