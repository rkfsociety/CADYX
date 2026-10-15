"""CADYX AI-first CAD foundation prototype."""

from .agent_runtime import AgentProvider, AgentRuntime
from .core import CadyxCore
from .advanced_features import install as _install_advanced_features

_install_advanced_features()

__all__ = ["AgentProvider", "AgentRuntime", "CadyxCore"]
