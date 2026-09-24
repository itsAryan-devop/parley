"""The trace — which is not telemetry, it is the scoring surface.

"Each scenario is scored 0-100 based strictly on trace logs." An action that is
not in the trace did not happen, and a call that vanishes mid-flight is
unscoreable and plausibly scored as a failure. Two consequences shape this
module:

1. **Append-only, never buffered past a step.** Whatever happened up to a crash
   must still be on disk.
2. **Every record carries virtual time and a monotonic sequence number.** Two
   records at the same millisecond still have a defined order, so latency
   measurement and replay are unambiguous.

Record kinds are deliberately few. `event` and `action` are the two queues of the
interface contract and are what a grader reads. `kernel` records are our own
decisions — why a call was cancelled, which slot invalidated it, what outcome an
effect resolved to. They cost nothing to emit and they are the difference between
a trace a human can audit and a pile of JSON.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

from pydantic import BaseModel, ConfigDict, Field


class RecordKind(str, Enum):
    EVENT = "event"
    """Inbound, from the harness."""
    ACTION = "action"
    """Outbound, from the agent."""
    KERNEL = "kernel"
    """An internal coordination decision, with its reason."""
    TOOL = "tool"
    """Mock-environment side: dispatch, completion, fault, latency."""
    NOTE = "note"
    """Free-form annotation. Never load-bearing."""


class TraceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: float
    """Virtual clock, milliseconds."""
    seq: int
    kind: RecordKind
    name: str
    """Discriminator within the kind: 'transcript_chunk', 'cancel', 'invalidate'…"""
    payload: dict[str, Any] = Field(default_factory=dict)


class Trace:
    """An append-only JSONL trace on the virtual timeline.

    Held open for the life of a scenario and flushed on every record. A scenario
    that blows the 120 s cap still leaves a complete trace up to the cut.
    """

    def __init__(self, path: str | Path | None = None, *, session_id: str = "") -> None:
        self.session_id = session_id
        self.records: list[TraceRecord] = []
        self._seq = 0
        self._path = Path(path) if path else None
        self._fh: TextIO | None = None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self._path.open("w", encoding="utf-8", newline="\n")

    # -- writing -----------------------------------------------------------

    # `t`, `kind` and `name` are positional-only so that a payload may use those
    # words as keys. A tool fault whose payload names its own `kind` is not an
    # exotic case, and silently colliding with the record header would be a
    # trace-corrupting bug in the one artefact the whole score is read from.

    def emit(self, t: float, kind: RecordKind, name: str, /, **payload: Any) -> TraceRecord:
        self._seq += 1
        rec = TraceRecord(t=t, seq=self._seq, kind=kind, name=name, payload=_jsonable(payload))
        self.records.append(rec)
        if self._fh is not None:
            self._fh.write(rec.model_dump_json() + "\n")
            self._fh.flush()
        return rec

    def event(self, t: float, name: str, /, **payload: Any) -> TraceRecord:
        return self.emit(t, RecordKind.EVENT, name, **payload)

    def action(self, t: float, name: str, /, **payload: Any) -> TraceRecord:
        return self.emit(t, RecordKind.ACTION, name, **payload)

    def kernel(self, t: float, name: str, /, **payload: Any) -> TraceRecord:
        return self.emit(t, RecordKind.KERNEL, name, **payload)

    def tool(self, t: float, name: str, /, **payload: Any) -> TraceRecord:
        return self.emit(t, RecordKind.TOOL, name, **payload)

    def note(self, t: float, name: str, /, **payload: Any) -> TraceRecord:
        return self.emit(t, RecordKind.NOTE, name, **payload)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> Trace:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- reading -----------------------------------------------------------

    def __iter__(self) -> Iterator[TraceRecord]:
        return iter(self.records)

    def __len__(self) -> int:
        return len(self.records)

    def of_kind(self, kind: RecordKind) -> list[TraceRecord]:
        return [r for r in self.records if r.kind is kind]

    def named(self, name: str, kind: RecordKind | None = None) -> list[TraceRecord]:
        return [
            r for r in self.records if r.name == name and (kind is None or r.kind is kind)
        ]

    def to_jsonl(self) -> str:
        return "".join(r.model_dump_json() + "\n" for r in self.records)

    @classmethod
    def load(cls, path: str | Path) -> Trace:
        """Re-open a written trace for scoring or visualisation."""
        tr = cls(None)
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = TraceRecord.model_validate_json(line)
                tr.records.append(rec)
                tr._seq = max(tr._seq, rec.seq)
        return tr


def _jsonable(obj: Any) -> Any:
    """Coerce pydantic models, enums and paths into plain JSON.

    Traces are read by a grader and by the viewer, neither of which imports our
    types, so nothing non-portable is allowed to reach the file.
    """
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return json.loads(json.dumps(obj, default=str))
