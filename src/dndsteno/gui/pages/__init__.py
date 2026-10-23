"""Sidebar pages, in display order. To add a page: create a Page subclass and list it here."""

from dndsteno.gui.pages.record import RecordPage
from dndsteno.gui.pages.sessions import SessionsPage
from dndsteno.gui.pages.settings import SettingsPage

PAGES = [RecordPage, SessionsPage, SettingsPage]
