"""Values that ULOO must agree with the Agno runtime on.

Agno is the only Team scheduler (see ``docs/ULOO_V2_IMPLEMENTATION_PLAN.md``
section 2.1), so ULOO may never accept a mode that Agno cannot execute.  The
pinned runtime (Agno 1.8.4) declares::

    mode: Literal["route", "coordinate", "collaborate"] = "coordinate"

Values live here as one explicit, greppable tuple rather than being derived by
import-time introspection, so that a broken or absent Agno install cannot
silently shrink your public contract.  ``tests/test_team_modes.py`` asserts this
module still matches the installed runtime, which turns an Agno upgrade into a
test failure instead of a run-time failure.
"""

TEAM_MODES: tuple[str, ...] = ("coordinate", "route", "collaborate")
"""Team modes ULOO accepts.  Must stay a subset of Agno's ``Team.mode`` Literal."""

TEAM_MODES_REQUIRING_LEADER: frozenset[str] = frozenset({"coordinate", "route"})
"""Modes where one agent drives delegation.

Agno keeps the coordinating voice on the ``Team`` object itself (``Team`` has no
``leader`` field in 1.8.4), so ``leader_agent_id`` is ULOO-level metadata: it
names the member whose role and instructions are used as that voice.
``collaborate`` runs every member in parallel and needs no such agent.
"""
