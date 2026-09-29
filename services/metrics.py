"""In-process counters rendered as Prometheus text."""

from __future__ import annotations


class Metrics:
    def __init__(self) -> None:
        self.counters: dict[str, float] = {}
        self.sums: dict[str, float] = {}
        self.obs_count: dict[str, float] = {}

    def inc(self, name: str, amount: float = 1) -> None:
        self.counters[name] = self.counters.get(name, 0.0) + amount

    def observe(self, name: str, value: float) -> None:
        self.sums[name] = self.sums.get(name, 0.0) + float(value)
        self.obs_count[name] = self.obs_count.get(name, 0.0) + 1

    def render(self) -> str:
        lines: list[str] = []
        for name in sorted(self.counters):
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {self.counters[name]}")
        for name in sorted(self.sums):
            lines.append(f"# TYPE {name} summary")
            lines.append(f"{name}_sum {self.sums[name]}")
            lines.append(f"{name}_count {self.obs_count.get(name, 0)}")
        return ("\n".join(lines) + "\n") if lines else "# no samples\n"


METRICS = Metrics()
