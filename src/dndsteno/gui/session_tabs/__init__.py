"""Tabs of the session view, in display order. To add one: subclass SessionTab and list it here."""

from dndsteno.gui.session_tabs.audio import AudioTab
from dndsteno.gui.session_tabs.obsidian import ObsidianTab
from dndsteno.gui.session_tabs.summary import SummaryTab
from dndsteno.gui.session_tabs.transcript import TranscriptTab

SESSION_TABS = [TranscriptTab, SummaryTab, ObsidianTab, AudioTab]
