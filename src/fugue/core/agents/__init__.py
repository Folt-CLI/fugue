"""The agents: what they are told, what they must answer with, and the tools they use to ask."""

from fugue.core.agents.prompts import Prompts, Role, Template
from fugue.core.agents.tools import Tool, UserTools

__all__ = ["Prompts", "Role", "Template", "Tool", "UserTools"]
