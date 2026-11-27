"""`fugue work N`: take one ticket through the whole flow."""

from pydantic_graph import Graph, GraphBuilder
from pydantic_graph.step import NodeStep

from fugue.core.github import Github
from fugue.core.reporting import Reporter
from fugue.core.work.config import WorkConfig
from fugue.core.work.result import WorkResult
from fugue.core.work.state import WorkDeps, WorkState
from fugue.core.work.steps import ReadTicket


class Work:
    """The work flow as a graph. A step that cannot go on raises, and the run stops there.

        result = await Work().run(5, reporter)

    The edges are the return types of the steps, so adding a step means writing it and changing
    the return type of the step before it. `diagram()` draws the flow.
    """

    def __init__(self, github: Github | None = None, config: WorkConfig | None = None) -> None:
        self.github = github or Github()
        self.config = config or WorkConfig.load()
        self._graph = self._build()

    async def run(self, number: int, reporter: Reporter) -> WorkResult:
        deps = WorkDeps(github=self.github, config=self.config, reporter=reporter)
        return await self._graph.run(state=WorkState(number=number), deps=deps, inputs=ReadTicket())

    def diagram(self) -> str:
        """The flow as a Mermaid state diagram."""
        return self._graph.render()

    @staticmethod
    def _build() -> Graph:
        builder = GraphBuilder(
            name="work",
            state_type=WorkState,
            deps_type=WorkDeps,
            input_type=ReadTicket,
            output_type=WorkResult,
        )
        builder.add_reachable(ReadTicket)
        builder.add(builder.edge_from(builder.start_node).to(NodeStep(ReadTicket)))
        return builder.build()
