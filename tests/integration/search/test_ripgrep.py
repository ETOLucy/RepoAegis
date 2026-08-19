import shutil

import pytest

from repo_maintenance_agent.search.adapters.ripgrep import (
    RipgrepSearch,
    default_lexical_search,
)
_HAVE_RG = shutil.which("rg") is not None
pytestmark = pytest.mark.skipif(not _HAVE_RG, reason="ripgrep binary not installed")
...
def test_default_lexical_search_factory_fails_fast_without_ripgrep(tmp_path: Path) -> None:
    import repo_maintenance_agent.search.adapters.ripgrep as module
    original = module.shutil.which
    module.shutil.which = lambda _name: None
    try:
        with pytest.raises(RuntimeError, match="ripgrep"):
            RipgrepSearch(tmp_path)
        with pytest.raises(RuntimeError, match="ripgrep"):
            default_lexical_search(tmp_path)
    finally:
        module.shutil.which = original