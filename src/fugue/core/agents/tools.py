"""The tools an agent uses to talk with the person running Fugue.

An agent that may ask (the analysis agent when it cannot go on, the interview agent all the time)
is given these tools. Each call is one question shown by the reporter, in whichever interface
runs the flow: buttons, a tick list, a text box or a form on the screen, a prompt in a terminal.
The call returns what the person answered, as JSON text the agent reads.

The tools are defined here once, apart from any agent. An adapter for an agent SDK wraps each
`Tool` in that SDK's way of declaring a tool, and calls `run` with the arguments the agent gave.

    tools = UserTools.for_role(Role.INTERVIEW, reporter, "interview")
    for tool in tools:
        print(tool.name, tool.schema)
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fugue.core.agents.prompts import Role
from fugue.core.reporting import Ask, Chat, Form, Reporter, Select
from fugue.errors import FlowError

type Handler = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    """A tool, as an agent SDK needs it described: a name, what it is for, the shape of its
    arguments, and the function that runs it."""

    name: str
    description: str
    schema: dict[str, Any]
    run: Handler


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AskInput(_Input):
    question: str = Field(description="The question, short and specific.")
    options: list[str] = Field(
        min_length=1, description="The choices. Put the one you would pick first."
    )
    default: str | None = Field(
        default=None, description="The preselected choice. One of the options."
    )
    other: str | None = Field(
        default=None,
        description="A label such as 'Something else', to let the person write their own answer.",
    )
    context: str = Field(
        default="",
        description="Markdown the person should read first, such as what you already checked.",
    )


class SelectInput(_Input):
    question: str = Field(description="The question, short and specific.")
    options: list[str] = Field(
        min_length=1, description="The choices. The person may tick any number."
    )
    selected: list[str] = Field(
        default_factory=list, description="The choices ticked at the start."
    )
    other: str | None = Field(
        default=None,
        description="A label such as 'Something else', to let the person add their own.",
    )
    context: str = Field(default="", description="Markdown the person should read first.")


class WriteInput(_Input):
    question: str = Field(description="What you want the person to write about.")
    placeholder: str = Field(default="", description="A hint of what a good answer looks like.")
    context: str = Field(default="", description="Markdown the person should read first.")


class FormField(_Input):
    name: str = Field(description="A short key for this question, used in the result.")
    kind: str = Field(description="One of: ask, select, write.")
    question: str
    options: list[str] = Field(default_factory=list, description="For ask and select.")
    default: str | None = Field(default=None, description="For ask: the preselected choice.")
    other: str | None = Field(
        default=None,
        description="For ask and select: a label that lets the person write their own.",
    )
    placeholder: str = Field(default="", description="For write.")


class FormInput(_Input):
    title: str = Field(description="What the form is about.")
    fields: list[FormField] = Field(min_length=1, description="The questions, answered together.")
    context: str = Field(default="", description="Markdown the person should read first.")


class TellInput(_Input):
    message: str = Field(description="What the person needs to know. Markdown.")


class ReadyInput(_Input):
    summary: str = Field(
        description="What you understood, and what is still open, so the person can confirm."
    )


class UserTools:
    """The question tools of one agent, bound to the reporter that shows them.

    `agent` names the agent, so the screen can say who is asking. `limit` is the most questions
    the agent may ask; past it, a tool refuses and tells the agent to signal that it is ready.
    """

    ALLOWED: ClassVar[dict[Role, tuple[str, ...]]] = {
        Role.ANALYSIS: ("ask_user", "select_from", "ask_user_to_write", "ask_user_form"),
        Role.INTERVIEW: (
            "ask_user",
            "select_from",
            "ask_user_to_write",
            "ask_user_form",
            "tell_user",
            "ready_to_analyse",
        ),
    }
    """The tools each role gets. The implementation and reviewer agents get none: they do not
    talk to the developer."""

    def __init__(self, reporter: Reporter, agent: str, *, limit: int | None = None) -> None:
        self._reporter = reporter
        self._agent = agent
        self._limit = limit
        self.asked = 0

    @classmethod
    def for_role(
        cls, role: Role, reporter: Reporter, agent: str, *, limit: int | None = None
    ) -> list[Tool]:
        """The tools this role may use, or none."""
        bound = cls(reporter, agent, limit=limit)
        names = cls.ALLOWED.get(role, ())
        return [tool for tool in bound.tools() if tool.name in names]

    def tools(self) -> list[Tool]:
        return [
            Tool(
                "ask_user",
                "Ask the developer to pick one of several choices. Use it when a few answers are "
                "possible and you can name them. Give an `other` label when your choices may not "
                "cover the answer.",
                AskInput.model_json_schema(),
                lambda args: self._call(AskInput, args, self._ask),
            ),
            Tool(
                "select_from",
                "Ask the developer to tick any number of choices.",
                SelectInput.model_json_schema(),
                lambda args: self._call(SelectInput, args, self._select),
            ),
            Tool(
                "ask_user_to_write",
                "Ask the developer for an answer in their own words.",
                WriteInput.model_json_schema(),
                lambda args: self._call(WriteInput, args, self._write),
            ),
            Tool(
                "ask_user_form",
                "Ask several related questions at once, answered together and submitted as one.",
                FormInput.model_json_schema(),
                lambda args: self._call(FormInput, args, self._form),
            ),
            Tool(
                "tell_user",
                "Show the developer something to read and acknowledge. Use it for information, "
                "not for a question.",
                TellInput.model_json_schema(),
                lambda args: self._call(TellInput, args, self._tell),
            ),
            Tool(
                "ready_to_analyse",
                "Say that you have enough to write the analysis. The developer confirms, or keeps "
                "talking. Call it again only when something new has been settled.",
                ReadyInput.model_json_schema(),
                lambda args: self._call(ReadyInput, args, self._ready),
            ),
        ]

    # ----- one tool each -----

    async def _ask(self, data: AskInput) -> str:
        if data.default is not None and data.default not in data.options:
            raise FlowError(f"`default` must be one of the options, and {data.default!r} is not")
        question = Ask(
            text=data.question,
            body=data.context,
            options=tuple(data.options),
            default=data.default or data.options[0],
            other=data.other,
            asked_by=self._agent,
        )
        answer = await self._reporter.ask(question)
        return self._json({"answer": answer.value, "typed_by_the_developer": answer.other})

    async def _select(self, data: SelectInput) -> str:
        question = Select(
            text=data.question,
            body=data.context,
            options=tuple(data.options),
            selected=tuple(s for s in data.selected if s in data.options),
            other=data.other,
            asked_by=self._agent,
        )
        answer = await self._reporter.ask(question)
        return self._json(
            {"answers": list(answer.values), "last_typed_by_the_developer": answer.other}
        )

    async def _write(self, data: WriteInput) -> str:
        question = Chat(
            text=data.question,
            body=data.context,
            placeholder=data.placeholder,
            asked_by=self._agent,
        )
        answer = await self._reporter.ask(question)
        return self._json({"answer": answer.value})

    async def _form(self, data: FormInput) -> str:
        fields: list[Ask | Select | Chat] = []
        for field in data.fields:
            match field.kind:
                case "ask":
                    if not field.options:
                        raise FlowError(f"the field {field.name!r} is an ask and needs options")
                    fields.append(
                        Ask(
                            text=field.question,
                            options=tuple(field.options),
                            default=field.default or field.options[0],
                            other=field.other,
                        )
                    )
                case "select":
                    if not field.options:
                        raise FlowError(f"the field {field.name!r} is a select and needs options")
                    fields.append(
                        Select(text=field.question, options=tuple(field.options), other=field.other)
                    )
                case "write":
                    fields.append(Chat(text=field.question, placeholder=field.placeholder))
                case other:
                    raise FlowError(
                        f"the field {field.name!r} has kind {other!r}: use ask, select or write"
                    )
        answer = await self._reporter.ask(
            Form(text=data.title, body=data.context, fields=tuple(fields), asked_by=self._agent)
        )
        result = {
            field.name: (list(each.values) if field.kind == "select" else each.value)
            for field, each in zip(data.fields, answer.answers, strict=True)
        }
        return self._json({"answers": result})

    async def _tell(self, data: TellInput) -> str:
        await self._reporter.ask(
            Ask(
                text=data.message.splitlines()[0] if data.message else "",
                body=data.message,
                options=("ok",),
                default="ok",
                asked_by=self._agent,
            )
        )
        return self._json({"acknowledged": True})

    async def _ready(self, data: ReadyInput) -> str:
        answer = await self._reporter.ask(
            Ask(
                text="Start the analysis?",
                body=data.summary,
                options=("yes", "no"),
                default="no",
                asked_by=self._agent,
            )
        )
        if answer.value == "yes":
            return self._json({"proceed": True})
        return self._json(
            {
                "proceed": False,
                "message": "The developer wants to keep talking. Carry on with the interview.",
            }
        )

    # ----- inside -----

    async def _call[T: BaseModel](
        self, model: type[T], args: dict[str, Any], run: Callable[[T], Awaitable[str]]
    ) -> str:
        """Check the arguments, count the question, and run it. A bad call comes back to the
        agent as an error it can read and fix, not as a crash."""
        try:
            data = model.model_validate(args)
        except ValidationError as error:
            return self._json({"error": f"the arguments are not valid: {error.errors()[0]['msg']}"})
        if self._limit is not None and self.asked >= self._limit:
            return self._json(
                {
                    "error": "You have used all your questions. Signal that you are ready, and "
                    "say what is still open."
                }
            )
        self.asked += 1
        try:
            return await run(data)
        except FlowError as error:
            self.asked -= 1
            return self._json({"error": str(error)})

    @staticmethod
    def _json(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False)
