"""Минимальный StateGraph с API как у LangGraph (add_node / add_edge /
add_conditional_edges / compile().invoke()), без внешних зависимостей.

Каждый узел — блок BPMN, развилка — условный переход. Прогон пишет трассу
(узел, тип, миллисекунды), которую фронт показывает в «Как думал агент».
"""
from __future__ import annotations

import time
from typing import Callable

END = "__end__"


class StateGraph:
    def __init__(self, name: str):
        self.name = name
        self.nodes: dict[str, dict] = {}
        self.edges: dict[str, str] = {}
        self.cond: dict[str, tuple[Callable, dict]] = {}
        self.entry: str | None = None

    def add_node(self, name: str, fn: Callable, kind: str = "code", label: str | None = None):
        self.nodes[name] = {"fn": fn, "kind": kind, "label": label or name}
        return self

    def set_entry_point(self, name: str):
        self.entry = name
        return self

    def add_edge(self, a: str, b: str):
        self.edges[a] = b
        return self

    def add_conditional_edges(self, a: str, router: Callable, mapping: dict):
        self.cond[a] = (router, mapping)
        return self

    def compile(self) -> "CompiledGraph":
        assert self.entry, "entry point not set"
        return CompiledGraph(self)


class CompiledGraph:
    def __init__(self, g: StateGraph):
        self.g = g

    def invoke(self, run: dict) -> dict:
        trace = run.setdefault("trace", [])
        node = self.g.entry
        steps = 0
        while node != END:
            steps += 1
            if steps > 50:
                raise RuntimeError(f"graph {self.g.name}: too many steps")
            spec = self.g.nodes[node]
            t0 = time.perf_counter()
            spec["fn"](run)
            trace.append({"graph": self.g.name, "node": node, "label": spec["label"], "kind": spec["kind"],
                          "ms": round((time.perf_counter() - t0) * 1000, 1)})
            if node in self.g.cond:
                router, mapping = self.g.cond[node]
                node = mapping[router(run)]
            else:
                node = self.g.edges.get(node, END)
        return run

    def mermaid(self) -> str:
        lines = [f"flowchart TD", f"  %% {self.g.name}"]
        shape = {"llm": ("([", "])"), "tool": ("[[", "]]"), "code": ("[", "]")}
        for n, spec in self.g.nodes.items():
            o, c = shape.get(spec["kind"], ("[", "]"))
            lines.append(f"  {n}{o}\"{spec['label']}\"{c}")
        lines.append(f"  {END}((конец))")
        for a, b in self.g.edges.items():
            lines.append(f"  {a} --> {b}")
        for a, (_, mapping) in self.g.cond.items():
            for label, b in mapping.items():
                lines.append(f"  {a} -- {label} --> {b}")
        return "\n".join(lines)
