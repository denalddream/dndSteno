"""Strip at the bottom of the window listing running background jobs."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from dndsteno.gui.jobs import Job, JobManager


class JobRow(QWidget):
    def __init__(self, job: Job):
        super().__init__()
        self.job = job
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)

        self.title = QLabel(f"<b>{job.title}</b>")
        self.bar = QProgressBar()
        self.bar.setFixedWidth(220)
        self.bar.setTextVisible(False)
        self.message = QLabel()
        self.message.setProperty("muted", True)
        self.cancel_btn = QPushButton(job.cancel_label)
        self.cancel_btn.clicked.connect(self._cancel)

        layout.addWidget(self.title)
        layout.addWidget(self.bar)
        layout.addWidget(self.message, 1)
        layout.addWidget(self.cancel_btn)

        job.progress.connect(self._on_progress)
        self._on_progress(job.last_fraction, job.last_message)

    def _on_progress(self, fraction: float, message: str) -> None:
        if fraction < 0:
            self.bar.setRange(0, 0)  # indeterminate
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(fraction * 1000))
        self.message.setText(message)

    def _cancel(self) -> None:
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Останавливается...")
        self.job.cancel()


class JobsBar(QWidget):
    def __init__(self, jobs: JobManager):
        super().__init__()
        self.setObjectName("jobsBar")
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 4, 0, 4)
        self.layout_.setSpacing(0)
        self.rows: dict[Job, JobRow] = {}
        jobs.job_started.connect(self._add)
        jobs.job_finished.connect(self._remove)
        self.setVisible(False)

    def _add(self, job: Job) -> None:
        row = JobRow(job)
        self.rows[job] = row
        self.layout_.addWidget(row)
        self.setVisible(True)

    def _remove(self, job: Job) -> None:
        row = self.rows.pop(job, None)
        if row:
            row.deleteLater()
        self.setVisible(bool(self.rows))
