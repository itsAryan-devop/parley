"""PARLEY ideas ported onto the Full-Duplex-Bench v3 LiveKit agent.

Two pieces, both pure Python so they are unit-tested without LiveKit or any
API key:

- `turn.ParleyTurnDetector` -- the learned endpointer (`parley.agent.endpointer`)
  exposed through LiveKit's turn-detector protocol, so the agent keeps listening
  through "Paris -- actually, no ..." instead of ending the turn on the abandoned
  value.
- `guard.ToolGuard` -- the ledger ideas from `parley.kernel`: a state-changing
  action is never issued twice, and a call is claimed only after a short commit
  window in which the user has not started speaking again (cancel before effect).

`fdb/agent_parley.py` wires both into a fork of FDB-v3's `cascaded_agent.py`.
"""

from .guard import ToolGuard
from .turn import ParleyTurnDetector, turn_features

__all__ = ["ToolGuard", "ParleyTurnDetector", "turn_features"]
