"""CADYX AI-first CAD foundation prototype."""

from .agent_runtime import AgentProvider, AgentRuntime
from .core import CadyxCore
from .advanced_features import install as _install_advanced_features
from .advanced_topology import install as _install_advanced_topology
from .agent_authoring import install as _install_agent_authoring
from .manufacturing_features import install as _install_manufacturing_features
from .semantic_selection import install as _install_semantic_selection
from .capability_refinements import install as _install_capability_refinements
from .scan_reverse import install as _install_scan_reverse

_install_advanced_features()
_install_advanced_topology()
_install_agent_authoring()
_install_manufacturing_features()
_install_semantic_selection()
_install_capability_refinements()
_install_scan_reverse()

__all__ = ["AgentProvider", "AgentRuntime", "CadyxCore"]
