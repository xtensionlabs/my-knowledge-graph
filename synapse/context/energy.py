"""Energy estimation — behavioral signal inference (M2 stub).

PRD §6.2 spec'd five signals (capture rate, capture length, task-switch rate,
time of day, recent review performance). M4 wires the full inference; M2
ships a deterministic stub that returns "medium" and updates the session
state's `energy_estimate` field on a schedule.

The stub still respects the contract: it's an async function, it persists
to session state, and the Synthesizer reads from session state — so swapping
the inference body in M4 requires no caller changes.

# CLARIFY: full energy signal inference deferred to M4 per PRD §6.2.
# Stub returns "medium" so the Synthesizer + Guardian pipelines don't block.
"""

from __future__ import annotations

from loguru import logger

from synapse.context.session import get_session, set_energy

DEFAULT_ENERGY: str = "medium"


async def refresh_energy_estimate() -> str:
    """Recompute the energy estimate and persist to session state.

    Returns:
        The current energy estimate ("low" | "medium" | "high").
    """
    # M2: always "medium". M4 will read signals and decide.
    snap = get_session()
    if snap.energy_estimate != DEFAULT_ENERGY:
        set_energy(DEFAULT_ENERGY)
    logger.debug("energy estimate = {e} (stub)", e=DEFAULT_ENERGY)
    return DEFAULT_ENERGY


def current_energy() -> str:
    """Synchronous read of the persisted energy estimate."""
    return get_session().energy_estimate or DEFAULT_ENERGY
