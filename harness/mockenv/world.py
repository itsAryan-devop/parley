"""Ground truth for the mock environment.

The world is kept separate from the tool dispatcher for one reason: it is the
*independent* record of what actually happened. The agent's state snapshot says
what the agent believes; `World` says what the environment did. Duplicate
state-changing calls are detected by comparing the two, so the scorer never has
to trust the agent's own account of itself.

Catalogue data is generated deterministically from the arguments — same query,
same results, every run, on every machine — because a replay harness whose tool
results drift is not a replay harness.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _seed(*parts: Any) -> int:
    """A stable integer from the arguments. `hash()` is salted per process; this is not."""
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


class Effect(BaseModel):
    """One committed side effect, as the *environment* saw it."""

    model_config = ConfigDict(extra="forbid")

    effect_id: str
    tool: str
    args: dict[str, Any]
    call_id: str
    t: float
    compensated_by: str | None = None
    """effect_id of the compensating effect that undid this one, if any."""

    @property
    def logical_key(self) -> str:
        """Identity of the *business* action, ignoring which call made it.

        Two effects sharing a logical key are a double-booking — regardless of
        call_id, retry count, or how the agent labelled them.
        """
        body = "|".join(f"{k}={self.args[k]!r}" for k in sorted(self.args))
        return f"{self.tool}({body})"


class World(BaseModel):
    """Session-scoped environment state. No cross-session carry-over."""

    model_config = ConfigDict(extra="forbid")

    effects: list[Effect] = Field(default_factory=list)
    _counter: int = 0

    # -- effects -----------------------------------------------------------

    def commit(self, tool: str, args: dict[str, Any], call_id: str, t: float) -> Effect:
        self._counter += 1
        eff = Effect(
            effect_id=f"eff{self._counter:04d}",
            tool=tool,
            args=dict(args),
            call_id=call_id,
            t=t,
        )
        self.effects.append(eff)
        return eff

    def compensate(self, target_tool: str, args: dict[str, Any], call_id: str, t: float) -> Effect | None:
        """Record a compensating effect and mark what it undid.

        Returns None when there is nothing live to undo — a compensation with no
        target is itself a bug worth surfacing rather than silently accepting.
        """
        probe = Effect(effect_id="probe", tool=target_tool, args=dict(args), call_id="", t=t)
        for eff in reversed(self.effects):
            if eff.compensated_by is None and eff.logical_key == probe.logical_key:
                undo = self.commit(f"compensate:{target_tool}", args, call_id, t)
                eff.compensated_by = undo.effect_id
                return undo
        return None

    # -- what the scorer asks ---------------------------------------------

    def live_effects(self) -> list[Effect]:
        """Effects that were committed and never undone."""
        return [e for e in self.effects if e.compensated_by is None and not e.tool.startswith("compensate:")]

    def duplicates(self) -> dict[str, list[Effect]]:
        """Logical keys committed more than once. Must be empty. Worth 10%."""
        by_key: dict[str, list[Effect]] = {}
        for eff in self.live_effects():
            by_key.setdefault(eff.logical_key, []).append(eff)
        return {k: v for k, v in by_key.items() if len(v) > 1}


# --------------------------------------------------------------------------
# Deterministic catalogues
# --------------------------------------------------------------------------

_CARRIERS = ["AI", "6E", "UK", "SG", "QP"]
_CABINS = ["economy", "premium_economy", "business"]


def flight_catalogue(origin: str, destination: str, date: str, limit: int = 4) -> list[dict[str, Any]]:
    """Reproducible flight results for a query.

    Deliberately includes both morning and afternoon departures so that a
    `REFINEMENT` ("morning flights only") can be satisfied by filtering an
    already-returned result set — which is the behaviour the refinement branch of
    the taxonomy exists to exercise.
    """
    s = _seed(origin, destination, date)
    out: list[dict[str, Any]] = []
    for i in range(limit):
        r = _seed(s, i)
        carrier = _CARRIERS[r % len(_CARRIERS)]
        # Alternate morning / afternoon so filtering always has something to do.
        hour = (6 + (r >> 3) % 5) if i % 2 == 0 else (13 + (r >> 3) % 7)
        out.append(
            {
                "flight_no": f"{carrier}{100 + (r >> 7) % 800}",
                "origin": origin,
                "destination": destination,
                "date": date,
                "depart": f"{hour:02d}:{((r >> 11) % 4) * 15:02d}",
                "duration_min": 75 + (r >> 13) % 180,
                "cabin": _CABINS[(r >> 17) % len(_CABINS)],
                "price_inr": 3200 + (r >> 19) % 9000,
                "seats_left": 1 + (r >> 23) % 9,
            }
        )
    return sorted(out, key=lambda f: f["depart"])


def hotel_catalogue(city: str, date: str, limit: int = 3) -> list[dict[str, Any]]:
    """A second domain, so goal switches have somewhere to switch *to*."""
    s = _seed("hotel", city, date)
    out = []
    for i in range(limit):
        r = _seed(s, i)
        out.append(
            {
                "hotel_id": f"H{1000 + (r >> 5) % 9000}",
                "name": ["The Grand", "Riverside Inn", "Airport Suites", "Old Town Lodge"][(r >> 9) % 4],
                "city": city,
                "date": date,
                "rating": round(3.0 + ((r >> 13) % 20) / 10, 1),
                "price_inr": 2200 + (r >> 15) % 7000,
            }
        )
    return sorted(out, key=lambda h: -h["rating"])


# Frame-grounded manual lookup. Keys are perception labels, not frame ids, so the
# vision path and the manual path meet at a label the agent must first *resolve*.
MANUAL_PAGES: dict[str, dict[str, Any]] = {
    "router_power_led_red": {
        "page": "RTR-4.2",
        "title": "Power LED solid red",
        "meaning": "The router failed its power-on self test.",
        "steps": [
            "Unplug the power adapter and wait 30 seconds.",
            "Plug it back in without the WAN cable attached.",
            "If the LED stays red, the unit needs replacement.",
        ],
    },
    "router_wan_led_amber": {
        "page": "RTR-5.1",
        "title": "WAN LED amber",
        "meaning": "The router has no upstream link.",
        "steps": [
            "Check the WAN cable is seated at both ends.",
            "Power-cycle the modem and wait two minutes.",
            "If still amber, contact your service provider.",
        ],
    },
    "washer_error_e4": {
        "page": "WM-9.7",
        "title": "Error code E4",
        "meaning": "Water inlet timeout - the drum did not fill in time.",
        "steps": [
            "Confirm the inlet tap is fully open.",
            "Straighten any kink in the inlet hose.",
            "Clean the inlet filter mesh.",
        ],
    },
    "washer_drum_noise": {
        "page": "WM-11.2",
        "title": "Grinding or rumbling during spin",
        "meaning": "Something is loose in the drum, or the bearings are worn.",
        "steps": [
            "Stop the cycle and check the drum for coins or clips.",
            "Confirm the machine is level on all four feet.",
            "If the noise persists when empty, the bearings need service.",
        ],
    },
    "tv_hdmi_no_signal": {
        "page": "TV-3.4",
        "title": "No signal on HDMI",
        "meaning": "The selected HDMI input has no active source.",
        "steps": [
            "Confirm the source device is powered on.",
            "Re-seat the HDMI cable at both ends.",
            "Try a different HDMI port and reselect the input.",
        ],
    },
}

# Sound classes the audio path recognises, mapped to a likely mechanical cause.
# Keyed by the classifier's own labels so the audio and diagnosis paths meet at
# a label the agent must first resolve -- and may have to ask about.
SOUND_DIAGNOSES: dict[str, dict[str, Any]] = {
    "beeping": {
        "page": "SND-1.1",
        "title": "Repeating beep",
        "meaning": "The appliance is signalling an unacknowledged alert.",
        "steps": [
            "Check the display for an error code.",
            "Press and hold the start button for three seconds to clear the alert.",
        ],
    },
    "continuous_tone": {
        "page": "SND-1.2",
        "title": "Steady tone",
        "meaning": "A stuck control-panel button, or a door sensor that never closed.",
        "steps": [
            "Open and firmly reclose the door.",
            "Wipe the control panel and check no button is depressed.",
        ],
    },
    "grinding": {
        "page": "SND-2.1",
        "title": "Grinding under load",
        "meaning": "Debris in the drum or worn bearings.",
        "steps": [
            "Run an empty cycle and listen again.",
            "Check the drum and filter for loose objects.",
            "If it grinds while empty, book a service visit.",
        ],
    },
    "clicking": {
        "page": "SND-3.1",
        "title": "Regular clicking",
        "meaning": "The relay is cycling, usually a failing water valve or thermostat.",
        "steps": [
            "Note whether the clicking follows the fill stage.",
            "Power-cycle at the wall and retry once.",
        ],
    },
    "silence": {
        "page": "SND-0.1",
        "title": "No sound at all",
        "meaning": "The unit is not receiving power, or is in standby.",
        "steps": [
            "Confirm the plug is seated and the socket is live.",
            "Hold the power button for five seconds.",
        ],
    },
}
