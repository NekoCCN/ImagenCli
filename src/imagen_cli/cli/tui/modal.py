"""Modal screens that only dismiss themselves while they are the current screen."""

from __future__ import annotations

from typing import TypeVar

from textual.await_complete import AwaitComplete
from textual.screen import ModalScreen

ScreenResult = TypeVar("ScreenResult")


class Modal(ModalScreen[ScreenResult]):
    """A second activation for one intent is ignored.

    Two activations can arrive for a single intent: a double click, or a key press
    queued behind a click. Textual's `dismiss` pops whatever screen is on top, so the
    second call raised ScreenStackError, or closed the screen that was underneath.
    """

    def dismiss(self, result: ScreenResult | None = None) -> AwaitComplete:
        if self.app.screen is not self:
            return AwaitComplete()
        return super().dismiss(result)
