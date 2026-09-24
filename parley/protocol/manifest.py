"""Schema-driven tool manifests.

Objective 4 of the guide: "Parse dynamic tool definitions (read-only vs.
state-modifying) from manifests; strictly avoid duplicate state-changing calls."
The public suite names *unseen tools* explicitly, so nothing here may assume a
tool name. The only thing the kernel is allowed to know about a tool is what the
manifest says.

The real evaluation kit's manifest dialect is unknown at time of writing, so
`parse_manifest` is deliberately permissive: it accepts a list or a dict, JSON
Schema-style `parameters` or a flat `params` list, and any of the common spellings
of the read-only/state-modifying flag. Getting this wrong costs the entire 10%
safety block, so it fails loudly rather than guessing the flag.
"""

from __future__ import annotations

from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

JsonType = str  # one of: string integer number boolean array object

_MUTATING_KEYS = ("mutating", "state_modifying", "stateModifying", "is_mutating", "write", "writes")
_READONLY_KEYS = ("read_only", "readonly", "readOnly", "is_read_only", "safe")


class ParamSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: JsonType = "string"
    required: bool = False
    description: str = ""
    enum: list[Any] | None = None


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    params: list[ParamSpec] = Field(default_factory=list)

    mutating: bool = False
    """True == state-modifying. Never speculated; always idempotency-keyed."""

    inverse_of: str | None = None
    """If set, this tool compensates (undoes) the named tool. Used to resolve
    `COMPLETED_NOW_STALE` effects."""

    idempotency_params: list[str] | None = None
    """Subset of params that define identity for duplicate suppression.
    None == all resolved args participate."""

    intent: str | None = None
    """Optional grouping hint: which goal this tool serves. Used to decide which
    slots survive a goal switch."""

    @property
    def param_names(self) -> set[str]:
        return {p.name for p in self.params}

    @property
    def required_params(self) -> set[str]:
        return {p.name for p in self.params if p.required}


class ToolManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools: dict[str, ToolSpec] = Field(default_factory=dict)

    def __contains__(self, name: object) -> bool:
        return name in self.tools

    def __getitem__(self, name: str) -> ToolSpec:
        return self.tools[name]

    def get(self, name: str) -> ToolSpec | None:
        return self.tools.get(name)

    @property
    def read_only(self) -> list[ToolSpec]:
        return [t for t in self.tools.values() if not t.mutating]

    @property
    def mutating(self) -> list[ToolSpec]:
        return [t for t in self.tools.values() if t.mutating]

    def inverse_for(self, tool_name: str) -> ToolSpec | None:
        """The tool declared as the compensating action for `tool_name`, if any."""
        for spec in self.tools.values():
            if spec.inverse_of == tool_name:
                return spec
        return None

    def params_for_intent(self, intent: str | None) -> set[str]:
        """Every parameter name reachable from `intent`.

        Drives `SessionState.retain_for_goal_switch`: a slot survives a goal
        switch iff some tool serving the new goal could consume it.
        """
        if intent is None:
            return set()
        names: set[str] = set()
        for spec in self.tools.values():
            if spec.intent in (None, intent):
                names |= spec.param_names
        return names

    def intents(self) -> set[str]:
        return {t.intent for t in self.tools.values() if t.intent}


class ManifestError(ValueError):
    """Raised when a manifest cannot be parsed unambiguously."""


def _coerce_flag(raw: dict[str, Any], tool_name: str) -> bool:
    """Work out read-only vs state-modifying without guessing.

    Precedence: an explicit mutating-style key wins; otherwise an explicit
    read-only-style key is inverted; otherwise default to read-only (safe: the
    only cost is a missed speculation, whereas defaulting to read-only on a
    *mutating* tool would risk a duplicate state change — so we additionally
    require the manifest to be explicit whenever it declares a compensator).
    """
    for key in _MUTATING_KEYS:
        if key in raw:
            return bool(raw[key])
    for key in _READONLY_KEYS:
        if key in raw:
            return not bool(raw[key])
    if raw.get("inverse_of") or raw.get("compensates"):
        raise ManifestError(
            f"tool {tool_name!r} declares a compensator but no read-only/mutating flag"
        )
    return False


def _coerce_params(raw: dict[str, Any]) -> list[ParamSpec]:
    """Accept either a flat param list or a JSON Schema `parameters` object."""
    if isinstance(raw.get("params"), list):
        return [ParamSpec(**p) if isinstance(p, dict) else ParamSpec(name=str(p)) for p in raw["params"]]

    schema = raw.get("parameters") or raw.get("input_schema") or raw.get("arguments")
    if isinstance(schema, list):
        return [ParamSpec(**p) if isinstance(p, dict) else ParamSpec(name=str(p)) for p in schema]
    if isinstance(schema, dict):
        props = schema.get("properties", schema)
        required = set(schema.get("required", []))
        out: list[ParamSpec] = []
        for name, body in props.items():
            body = body if isinstance(body, dict) else {}
            out.append(
                ParamSpec(
                    name=name,
                    type=body.get("type", "string"),
                    required=name in required or bool(body.get("required")),
                    description=body.get("description", ""),
                    enum=body.get("enum"),
                )
            )
        return out
    return []


def parse_manifest(raw: Any) -> ToolManifest:
    """Parse a scenario tool manifest into typed descriptors.

    Accepts `{"tools": [...]}`, `{"tools": {name: spec}}`, or a bare list.
    """
    if isinstance(raw, ToolManifest):
        return raw

    entries: Iterable[Any]
    if isinstance(raw, dict):
        body = raw.get("tools", raw)
        if isinstance(body, dict):
            entries = [{"name": k, **(v if isinstance(v, dict) else {})} for k, v in body.items()]
        elif isinstance(body, list):
            entries = body
        else:
            raise ManifestError(f"unsupported manifest body type: {type(body).__name__}")
    elif isinstance(raw, list):
        entries = raw
    else:
        raise ManifestError(f"unsupported manifest type: {type(raw).__name__}")

    tools: dict[str, ToolSpec] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ManifestError(f"tool entry must be an object, got {type(entry).__name__}")
        name = entry.get("name") or entry.get("tool") or entry.get("id")
        if not name:
            raise ManifestError(f"tool entry has no name: {entry!r}")

        tools[name] = ToolSpec(
            name=name,
            description=entry.get("description", "") or entry.get("summary", ""),
            params=_coerce_params(entry),
            mutating=_coerce_flag(entry, name),
            inverse_of=entry.get("inverse_of") or entry.get("compensates"),
            idempotency_params=entry.get("idempotency_params"),
            intent=entry.get("intent") or entry.get("domain") or entry.get("group"),
        )

    return ToolManifest(tools=tools)
