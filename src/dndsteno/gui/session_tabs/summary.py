from __future__ import annotations

import copy
import re

from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextBrowser, QVBoxLayout

from dndsteno.gui.jobs import Job
from dndsteno.gui.session_tabs.base import SessionTab
from dndsteno.obsidian import render_session_note
from dndsteno.pipeline import summarize
from dndsteno.summarize import SummaryProgress, load_summary

# Share of the progress bar per stage.
STAGE_WEIGHTS = {"map": (0.0, 0.8), "reduce": (0.8, 0.1), "dedupe": (0.9, 0.1)}
STAGE_NAMES = {"map": "читаю расшифровку", "reduce": "собираю итог", "dedupe": "сверяю со статьями"}


class SummaryTab(SessionTab):
    title = "Саммари"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.status = QLabel()
        self.status.setProperty("muted", True)
        self.status.setWordWrap(True)
        self.run_btn = QPushButton("Сделать саммари")
        self.run_btn.setProperty("role", "primary")
        self.run_btn.clicked.connect(lambda: self.start())
        self.rerun_btn = QPushButton("Сделать заново")
        self.rerun_btn.setToolTip("Не использовать сохранённые результаты по кускам")
        self.rerun_btn.clicked.connect(lambda: self.start(force=True))

        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        bar.addWidget(self.rerun_btn)
        bar.addWidget(self.run_btn)

        self.view = QTextBrowser()
        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.view, 1)

        ctx.jobs.job_started.connect(lambda _: self._update_buttons())
        ctx.jobs.job_finished.connect(lambda _: self._update_buttons())

    def _update_buttons(self) -> None:
        s = self.session
        has_transcript = bool(s and s.transcript_json.exists())
        busy = bool(self.ctx.jobs.running("summarize"))
        self.run_btn.setEnabled(has_transcript and not busy)
        self.rerun_btn.setEnabled(has_transcript and not busy and bool(s and s.summary_json.exists()))
        if s and not has_transcript:
            self.run_btn.setToolTip("Сначала расшифруйте сессию")
        elif busy:
            self.run_btn.setToolTip("Нейросеть уже занята другой сессией")
        else:
            self.run_btn.setToolTip("")

    def load(self, session) -> None:
        super().load(session)
        self._update_buttons()
        summary = load_summary(session) if session else None
        if summary is None:
            self.view.clear()
            self.status.setText("Саммари ещё нет." if session and session.transcript_json.exists()
                                else "Саммари делается по расшифровке — сначала расшифруйте сессию.")
            return
        self.status.setText(f"Сделано {summary.get('created', '').replace('T', ' ')} · модель {summary.get('model')}")
        md = render_session_note(self.ctx.config, summary)
        if md.startswith("---"):
            md = md.split("---", 2)[2]  # hide frontmatter in the preview
        # Obsidian hides the %% marker %% and styles the callout; Qt shows both raw, so drop them here.
        md = re.sub(r"^> %%.*%%\n", "", md, flags=re.MULTILINE)
        md = md.replace(f"[!{self.ctx.config.obsidian.callout}] ", "")
        facts = []
        for e in summary.get("entities", []):
            where = "новая статья" if not e.get("note") else e["note"]
            facts.append(f"\n### {e['name']}  ·  _{where}_")
            facts += [f"- {f['fact']} `{f.get('time', '')}`" for f in e["facts"]]
        if facts:
            md += "\n---\n## Предложенные дополнения статей\n" + "\n".join(facts)
        self.view.setMarkdown(md)

    def start(self, force: bool = False) -> None:
        session = self.session
        if session is None or not session.transcript_json.exists() or self.ctx.jobs.running("summarize"):
            return
        cfg = copy.deepcopy(self.ctx.config)

        def work(job: Job):
            job.report(-1, "подключаюсь к нейросети…")

            def on_progress(p: SummaryProgress):
                base, weight = STAGE_WEIGHTS[p.stage]
                fraction = base + weight * (p.index - 1 + (1 if p.cached else 0.5 if p.chars else 0)) / p.count
                chars = f" · получено {p.chars} симв." if p.chars else ""
                job.report(min(fraction, 0.99), f"{STAGE_NAMES[p.stage]} {p.index}/{p.count}{chars}")

            return summarize(cfg, session, force=force, on_progress=on_progress, cancel=job.cancel_event)

        self.ctx.jobs.start(
            f"Саммари: {session.name}", work, kind="summarize", meta={"session": session.name},
            on_success=lambda _: self.ctx.session_changed.emit(session.name),
            on_error=lambda e: QMessageBox.warning(self, "Ошибка саммари", e),
        )
