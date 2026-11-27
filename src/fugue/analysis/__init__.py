"""Analysis handoffs, Markdown rendering and durable stage execution.

The stage lives in `fugue.analysis.stage`; it is not re-exported here because the engine
imports the scoping renderers from this package.
"""

from fugue.analysis.schema import Handoff, handoff_schema

__all__ = ["Handoff", "handoff_schema"]
