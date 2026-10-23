"""Tabs of the session view, in display order. To add one: subclass SessionTab and list it here."""

from dndsteno.gui.session_tabs.audio import AudioTab
from dndsteno.gui.session_tabs.transcript import TranscriptTab

SESSION_TABS = [TranscriptTab, AudioTab]
