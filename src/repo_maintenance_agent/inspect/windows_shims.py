"""Windows compatibility shims for swebench on Windows (no ``resource`` module)."""
from __future__ import annotations

import sys
import types


def install_windows_shims() -> None:
    """Inject a minimal ``resource`` module so swebench imports on Windows.

    swebench.harness.prepare_images and run_evaluation call
    ``resource.setrlimit(resource.RLIMIT_NOFILE, ...)`` at import or run time.
    On Windows this module does not exist; the call is a no-op there because
    the OS does not expose that limit the same way.
    """
    if "resource" in sys.modules:
        return
    resource = types.ModuleType("resource")
    resource.RLIMIT_NOFILE = 7  # type: ignore[attr-defined]  # arbitrary constant; unused by our no-op

    def setrlimit(resource_type: int, limits: tuple[int, int]) -> None:
        del resource_type, limits  # no-op on Windows

    resource.setrlimit = setrlimit  # type: ignore[attr-defined]
    sys.modules["resource"] = resource
