"""Bounded human and automatic interviews using the chat component."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from rich.console import RenderableType

from fugue.agents import AgentEvent, AgentRequest, AgentResult, ProviderAdapter
from fugue.cli.chat import ChatSession, default_reader
from fugue.config.schema import Limits
from fugue.interview.prompts import StandInReply
from fugue.interview.record import InterviewRecord, Speaker
from fugue.interview.render import render_conversation
from fugue.interview.store import InterviewStore
from fugue.models import Role
from fugue.workflow import RunState


class _TimedAdapter:
    def __init__(
        self,
        adapter: ProviderAdapter,
        seconds: int,
        on_cancel: Callable[[AgentResult], None] | None = None,
    ) -> None:
        self.on_cancel = on_cancel
        self.adapter = adapter
        self.seconds = seconds
        self.name = adapter.name
        self.authentication_mode = adapter.authentication_mode
        self.capabilities = adapter.capabilities

    async def run(
        self,
        request: AgentRequest,
        on_event: Callable[[AgentEvent], Awaitable[None]] | None = None,
    ) -> AgentResult:
        try:
            async with asyncio.timeout(self.seconds):
                return await self.adapter.run(request, on_event)
        except (KeyboardInterrupt, asyncio.CancelledError):
            if self.on_cancel is not None:
                self.on_cancel(self._failure("Interview interrupted", cancelled=True))
            raise
        except TimeoutError:
            return self._failure("Interview call timed out", timed_out=True)
        except Exception as error:
            return self._failure(str(error))

    def _failure(
        self, error: str, *, timed_out: bool = False, cancelled: bool = False
    ) -> AgentResult:
        return AgentResult(
            ok=False,
            final_text="",
            structured_output=None,
            session_id=None,
            usage=None,
            provider=self.name,
            authentication_mode=self.authentication_mode,
            error=error,
            timed_out=timed_out,
            cancelled=cancelled,
            log_path=None,
            unsupported=None,
        )


class _InterviewChat(ChatSession):
    @property
    def transcript(self) -> str:
        return self.conversation()

    conversation: Callable[[], str]


class InterviewSession:
    def __init__(
        self,
        record: InterviewRecord,
        store: InterviewStore,
        analyst: ProviderAdapter,
        make_request: Callable[[Role, str], AgentRequest],
        *,
        limits: Limits | None = None,
        standin: ProviderAdapter | None = None,
        reader: Callable[[str], str] = default_reader,
        print_fn: Callable[[RenderableType], None] = print,
        on_result: Callable[[Role, AgentResult], None] | None = None,
    ) -> None:
        self.record = record
        self.store = store
        self.limits = limits or Limits()
        self.analyst = _TimedAdapter(
            analyst,
            self.limits.interview_call_timeout_seconds,
            lambda result: self._result(Role.ANALYST, result),
        )
        self.standin = (
            _TimedAdapter(
                standin,
                self.limits.interview_call_timeout_seconds,
                lambda result: self._result(Role.STAND_IN, result),
            )
            if standin
            else None
        )
        self.make_request = make_request
        self.reader = reader
        self.print = print_fn
        self.on_result = on_result
        self.state = RunState.INTERRUPTED
        self.reason = ""
        self.failed = False
        self.speaker: Speaker = "person"
        self.analyst_calls = 0

    def _result(self, role: Role, result: AgentResult) -> None:
        if role == Role.STAND_IN:
            self.record.standin_provider = result.provider
        if self.on_result:
            self.on_result(role, result)
        self.store.save(self.record)
        if not result.ok or result.provider_limited or result.timed_out or result.cancelled:
            self.failed = True
            self.state = (
                RunState.PROVIDER_LIMITED
                if result.provider_limited
                else RunState.INTERRUPTED
                if result.cancelled
                else RunState.FAILED
            )
            self.reason = result.error or "Interview provider failed"

    def _request(self, text: str) -> AgentRequest:
        if self.analyst_calls >= self.limits.interview_max_turns + 1:
            raise ValueError("Interview analyst call limit reached")
        if self.record.turns:
            self.record.add_turn(self.speaker, text, max_turns=self.limits.interview_max_turns)
            self.store.save(self.record)
        self.analyst_calls += 1
        return self.make_request(Role.ANALYST, text)

    def _turn(self, question: str, answer: str) -> None:
        self.record.add_turn("analyst", answer, max_turns=self.limits.interview_max_turns)
        self.store.save(self.record)

    def _read(self, prompt: str) -> str:
        if self.failed:
            raise EOFError
        return self.reader(prompt)

    async def _proceed(self, argument: str = "") -> bool:
        self.record.status = "proceeded"
        self.record.ended_by = "stand_in" if self.speaker == "stand_in" else "person"
        self.record.finished = datetime.now(UTC)
        self.store.save(self.record)
        self.state = RunState.WAITING_FOR_SELECTION
        return True

    async def run(self, *, automatic: bool = False) -> RunState:
        if self.record.status == "proceeded":
            return RunState.WAITING_FOR_SELECTION
        self.record.mode = "automatic" if automatic else "human"
        self.speaker = "stand_in" if automatic else "person"
        self.store.save(self.record)
        chat = _InterviewChat(
            self.analyst,
            self._request,
            reader=self._read,
            emit=self.print,
            commands={"/proceed": self._proceed},
            max_turns=self.limits.interview_max_turns,
            on_result=lambda result: self._result(Role.ANALYST, result),
            on_turn=self._turn,
        )
        chat.conversation = lambda: render_conversation(self.record)
        chat.turns = [("", "")] * self.record.turn_count
        try:
            if automatic:
                async with asyncio.timeout(self.limits.interview_automatic_max_seconds):
                    await self._automatic(chat)
            else:
                if not self.record.turns:
                    await chat.ask("")
                    chat.turns.clear()
                if not self.failed:
                    await chat.run()
        except TimeoutError:
            self.state = RunState.BLOCKED
            self.reason = "Automatic interview time limit reached; continue in human mode"
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.state = RunState.INTERRUPTED
        except Exception as error:
            self.state = RunState.FAILED
            self.reason = str(error)
        finally:
            self.store.save(self.record)
        if self.reason:
            self.print(self.reason)
        return self.state

    async def _automatic(self, chat: ChatSession) -> None:
        if self.standin is None:
            raise ValueError("Automatic interview requires a stand-in")
        if not self.record.turns:
            await chat.ask("")
        if self.failed:
            return
        for _ in range(self.limits.interview_max_turns - self.record.turn_count):
            result = await self.standin.run(self.make_request(Role.STAND_IN, ""))
            self._result(Role.STAND_IN, result)
            if self.failed:
                return
            reply = (
                StandInReply.model_validate(result.structured_output)
                if result.structured_output is not None
                else StandInReply.model_validate_json(result.final_text)
            )
            if reply.needs_human:
                self.record.add_turn(
                    "stand_in",
                    reply.reply,
                    max_turns=self.limits.interview_max_turns,
                    needs_human=True,
                )
                self.state = RunState.BLOCKED
                self.reason = reply.reason or "Interview needs human input"
                return
            if reply.proceed:
                self.record.add_turn(
                    "stand_in", reply.reply, max_turns=self.limits.interview_max_turns
                )
                await self._proceed()
                return
            await chat.ask(reply.reply)
            if self.failed:
                return
        self.print("Interview turn limit reached; proceeding to analysis.")
        await self._proceed()
        self.record.ended_by = "turn_limit"
