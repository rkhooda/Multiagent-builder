"""Typed error taxonomy for the pipeline.

call_llm raises the LLM* classes; the stage_node boundary wraps anything else
as AgentError. run_graph_background applies policy by error_type.
"""


class LLMError(Exception):
    """Base for classified LLM-call failures."""

    error_type = "llm_error"
    recoverable = True

    def __init__(self, message: str, agent_type: str = "", model: str = ""):
        self.agent_type = agent_type
        self.model = model
        super().__init__(message)


class LLMTimeoutError(LLMError):
    error_type = "timeout"


class LLMRateLimitError(LLMError):
    error_type = "rate_limit"


class LLMAuthError(LLMError):
    error_type = "auth"


class LLMOutputError(LLMError):
    """Bad/empty/unparseable output that survived one repair attempt."""

    error_type = "bad_output"


class AgentError(Exception):
    """A bug in our own agent code — never auto-retried."""

    error_type = "agent_bug"
    recoverable = False

    def __init__(self, message: str, agent_type: str = "", model: str = ""):
        self.agent_type = agent_type
        self.model = model
        super().__init__(message)
