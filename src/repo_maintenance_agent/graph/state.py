from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from repo_maintenance_agent.domain.models import RepoTaskState


class GraphState(TypedDict):
    task: RepoTaskState
    trace: Annotated[list[str], operator.add]
