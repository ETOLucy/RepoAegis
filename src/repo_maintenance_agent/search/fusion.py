from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from repo_maintenance_agent.domain.models import SearchHit


def reciprocal_rank_fusion(
    result_sets: Sequence[Sequence[SearchHit]],
    *,
    limit: int,
    rank_constant: int = 60,
) -> list[SearchHit]:
    scores: dict[str, float] = defaultdict(float)
    hits: dict[str, SearchHit] = {}
    sources: dict[str, list[str]] = defaultdict(list)

    for result_set in result_sets:
        for rank, item in enumerate(result_set, start=1):
            scores[item.hit_id] += 1.0 / (rank_constant + rank)
            hits.setdefault(item.hit_id, item)
            if item.source not in sources[item.hit_id]:
                sources[item.hit_id].append(item.source)

    ranked = sorted(scores, key=lambda hit_id: (-scores[hit_id], hit_id))[:limit]
    return [
        hits[hit_id].model_copy(
            update={
                "score": scores[hit_id],
                "source": "+".join(sorted(sources[hit_id])),
            }
        )
        for hit_id in ranked
    ]
