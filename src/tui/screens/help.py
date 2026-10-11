"""Help view — key bindings and commands."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, Static

from src.tui.screens.base import ModalBase

__all__ = ["HelpScreen"]

_HELP = """[b]Keys[/b]
  a  agents      t  tasks      m  monitor      l  logs
  ?  help        q  quit

[b]Commands[/b] (type in the input bar)
  :agents   :tasks   :monitor   :logs   :help   :quit

[b]Running an agent[/b]
  @coder  write a fibonacci function     → run one agent
  explain this error                     → run the default agent
"""


class HelpScreen(ModalBase):
    title_text = "Help"

    def compose(self) -> ComposeResult:
        with Vertical(id="help-modal"):
            yield Label(self.title_text, classes="modal-title")
            yield Static(_HELP)
            yield Label("press esc to close", classes="modal-hint")
