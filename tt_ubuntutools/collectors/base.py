from __future__ import annotations

from ..desktop_index import DesktopIndex
from ..models import SoftwareItem


class Collector:
    """Một nguồn phần mềm. available() = công cụ có trên máy; collect() = danh sách phần mềm."""

    name = "base"
    label = ""

    def available(self) -> bool:
        return True

    def collect(self, index: DesktopIndex) -> list[SoftwareItem]:
        raise NotImplementedError
