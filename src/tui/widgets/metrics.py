"""Monitor widget rendering live system metrics (CPU, memory, battery, uptime)."""

from __future__ import annotations

from typing import Any

from rich.table import Table
from textual.widgets import Static

from src.core import sysinfo

__all__ = ["MetricsPanel"]


class MetricsPanel(Static):
    """Shows a snapshot of the host machine's vital signs."""

    def __init__(self, llm_calls: int = 0, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._llm_calls = llm_calls

    def refresh_panel(self, llm_calls: int | None = None) -> None:
        if llm_calls is not None:
            self._llm_calls = llm_calls
        self.update(self.build_table())

    def build_table(self) -> Table:
        snapshot = sysinfo.snapshot()
        table = Table(title="System", expand=True)
        table.add_column("Metric")
        table.add_column("Value", justify="right")

        load = snapshot["load_average"]
        table.add_row("CPU cores", str(snapshot["cpu_count"]))
        table.add_row("Load (1/5/15)", " ".join(f"{x:.2f}" for x in load) if load else "n/a")

        mem = snapshot["memory"]
        if mem:
            table.add_row(
                "Memory",
                f"{mem['used_kb'] // 1024} / {mem['total_kb'] // 1024} MB ({mem['used_percent']}%)",
            )

        battery = snapshot["battery"]
        if battery:
            table.add_row("Battery", f"{battery['percent']}% ({battery['status']})")

        table.add_row("Uptime", f"{snapshot['uptime'] / 3600:.1f} h")
        table.add_row("LLM calls", str(self._llm_calls))
        return table
