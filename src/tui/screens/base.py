"""A titled modal dialog base shared by the TUI screens."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.screen import ModalScreen

if TYPE_CHECKING:  # avoid a runtime import cycle (app imports the screens)
    from src.tui.app import ZyvoTUI

__all__ = ["ModalBase"]


class ModalBase(ModalScreen[None]):
    """Modal dismissed with ``esc``/``q``; exposes a typed :attr:`zyvo` handle."""

    BINDINGS = [("escape", "close", "Close"), ("q", "close", "Close")]
    title_text: str = "Dialog"

    DEFAULT_CSS = """
    ModalBase {
        align: center middle;
    }
    """

    @property
    def zyvo(self) -> ZyvoTUI:
        """The owning application, correctly typed for subclass screen code."""
        return cast("ZyvoTUI", self.app)

    def action_close(self) -> None:
        self.dismiss(None)
