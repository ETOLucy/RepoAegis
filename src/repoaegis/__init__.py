"""RepoAegis: a policy-controlled issue-to-PR coding agent.

Three top-level packages, one per skill line:

- ``agent``  -- the solving loop: ReAct, tools, context compaction.
- ``server`` -- the backend: state machine, approval, storage, events, HTTP API.
- ``eval``   -- the harness that proves the claims the other two make.

The console lives outside this package in ``web/``.
"""

__version__ = "0.1.0"
