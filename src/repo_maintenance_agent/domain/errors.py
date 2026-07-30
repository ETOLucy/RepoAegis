class RepoAgentError(Exception):
    """Base error for errors safe to classify at orchestration boundaries."""


class InvalidStateTransition(RepoAgentError):
    """Raised when a task attempts an illegal lifecycle transition."""


class AuthorizationDenied(RepoAgentError):
    """Raised when policy denies a tool call."""


class ToolExecutionError(RepoAgentError):
    """Raised when an adapter cannot safely complete a tool call."""


class ConcurrentUpdate(RepoAgentError):
    """Raised when optimistic state version validation fails."""


class ResourceNotFound(RepoAgentError):
    """Raised without revealing whether a cross-tenant resource exists."""


class LeaseConflict(RepoAgentError):
    """Raised when a worker presents an expired or superseded queue lease."""
