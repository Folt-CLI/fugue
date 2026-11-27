"""The base of every flow."""

from abc import ABC
from datetime import UTC, datetime
from types import UnionType
from typing import Any, ClassVar, Union, get_args, get_origin, get_type_hints
from uuid import uuid4

from pydantic_graph import Graph, GraphBuilder
from pydantic_graph.step import NodeStep

from fugue.core.flow.checkpoint import Checkpoint
from fugue.core.flow.config import FlowConfig
from fugue.core.flow.records import RunReporter
from fugue.core.flow.state import FlowDeps
from fugue.core.flow.step import Step
from fugue.core.github import Github
from fugue.core.reporting import Reporter, RunFailed, RunFinished, RunStarted, RunStopped
from fugue.errors import FlowError, FlowStopped, FugueError


class Flow[ConfigT: FlowConfig, StateT, ResultT](ABC):
    """A workflow: a graph of steps run with a state, some dependencies and a reporter.

    A flow names itself, its configuration, its state and result types and its first step, and
    gives a typed `run` that builds the state and calls `execute`. Everything around a run is
    done here once: the graph is built from the steps' return types, every event is stamped
    with the run and flow and recorded, a step that raises stops the run, and the run's start,
    end and failure are reported.

        class Work(Flow[WorkConfig, WorkState, WorkResult]):
            name = "work"
            config_type = WorkConfig
            state_type = WorkState
            result_type = WorkResult
            start = ReadTicketNode

            async def run(self, number: int, reporter: Reporter) -> WorkResult:
                return await self.execute(WorkState(number=number), reporter, f"#{number}")
    """

    name: ClassVar[str]
    config_type: ClassVar[type[FlowConfig]]
    state_type: ClassVar[type[Any]]
    result_type: ClassVar[type[Any]]
    start: ClassVar[type[Step[Any, Any, Any]]]

    def __init__(self, github: Github | None = None, config: ConfigT | None = None) -> None:
        self.github = github or Github()
        self.config: ConfigT = config or self.config_type.load()  # type: ignore[assignment]
        self._graph = self._build()

    async def execute(self, state: StateT, reporter: Reporter, subject: str) -> ResultT:
        """Run the flow from its first step. `subject` says what it is about, for the record."""
        run_id = self._new_run_id()
        scoped = RunReporter(
            reporter,
            run_id=run_id,
            flow=self.name,
            record=self._record(run_id),
            auto=self.config.auto,
        )
        deps = FlowDeps(
            github=self.github,
            config=self.config,
            reporter=scoped,
            checkpoint=Checkpoint(scoped),
        )
        await scoped.report(RunStarted(subject=subject))
        try:
            result = await self._graph.run(state=state, deps=deps, inputs=self.start())
        except FlowStopped as stopped:
            await scoped.report(RunStopped(message=str(stopped)))
            raise
        except FugueError as error:
            await scoped.report(RunFailed(message=str(error)))
            raise
        except Exception as error:
            message = f"{self.name} failed unexpectedly: {error!r}"
            await scoped.report(RunFailed(message=message))
            raise FlowError(message) from error
        await scoped.report(RunFinished())
        return result

    def steps(self) -> list[str]:
        """The name of every step the flow can run, in the order the graph reaches them.

        An interface shows this as the flow's checklist before anything has run."""
        found: list[type[Step[Any, Any, Any]]] = []
        queue = [self.start]
        while queue:
            node = queue.pop(0)
            if node not in found:
                found.append(node)
                queue.extend(self._next(node))
        return [node.name for node in found]

    @staticmethod
    def _next(node: type[Step[Any, Any, Any]]) -> list[type[Step[Any, Any, Any]]]:
        """The steps a step can lead to: the classes in the return type of its `run`."""
        hint = get_type_hints(node.run).get("return")
        options = get_args(hint) if get_origin(hint) in (Union, UnionType) else (hint,)
        return [o for o in options if isinstance(o, type) and issubclass(o, Step)]

    def diagram(self) -> str:
        """The flow as a Mermaid state diagram."""
        return self._graph.render()

    def _build(self) -> Graph:
        builder = GraphBuilder(
            name=self.name,
            state_type=self.state_type,
            deps_type=FlowDeps,
            input_type=self.start,
            output_type=self.result_type,
        )
        builder.add_reachable(self.start)
        builder.add(builder.edge_from(builder.start_node).to(NodeStep(self.start)))
        return builder.build()

    def _new_run_id(self) -> str:
        return f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{self.name}-{uuid4().hex[:6]}"

    def _record(self, run_id: str) -> Any:
        if not self.config.record_runs:
            return None
        folder = self.config.runs_dir or self.github.repository.root / ".fugue" / "runs"
        return folder / f"{run_id}.jsonl"
