"""The prompts and templates that ship with Fugue, and how they are put together.

They are plain Markdown in `fugue/prompts`:

- `agents/<role>.md`: what an agent is told: analysis, scoping, interview, implementation and
  reviewer. General on purpose, so it can be reused anywhere.
- `responses/ticket-format/<type>.md`: the ticket body an agent writes for the person, one
  template per issue type.
- `artifacts/<name>.md`: a file an agent writes for another agent, such as the solution.

An agent's instructions are, in this order: the general rules of writing and communicating
(`system.md`, the same for every agent), the built-in prompt for its role, and the person's own
guidance for that role if they set some (`agents.analysis = /some/path/guidance.md`). The guidance
is appended last, so it adds to the instructions and can never replace them.

A response or an artifact must follow its template, and `Template.missing` says which parts an
answer left out.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from fugue.config import AgentsConfig
from fugue.configfiles import ConfigFiles
from fugue.errors import ConfigError

ROOT: Traversable = files("fugue") / "prompts"


class Role(StrEnum):
    """What an agent is for. The value is the name of its prompt and of its setting."""

    ANALYSIS = "analysis"  # full analysis of an M ticket or an M Question
    SCOPING = "scoping"  # an S ticket: decides nothing, returns the scope
    INTERVIEW = "interview"  # an L Question: talks with the developer
    IMPLEMENTATION = "implementation"
    REVIEWER = "reviewer"


@dataclass(frozen=True)
class Heading:
    """A heading of a template. `repeats` marks one that appears one or more times (it has a
    placeholder in it, like an option's name); `optional` marks a section that may be left out."""

    text: str
    repeats: bool = False
    optional: bool = False


@dataclass(frozen=True)
class Template:
    """A response or artifact an agent must follow."""

    name: str
    text: str

    @property
    def headings(self) -> tuple[Heading, ...]:
        """The headings an answer must have, in order, read from the template."""
        found: list[Heading] = []
        lines = [line for line in self.text.splitlines()]
        in_comment = False
        for number, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("<!--"):
                in_comment = "-->" not in stripped
                continue
            if in_comment:
                in_comment = "-->" not in stripped
                continue
            match = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
            if match is None:
                continue
            raw = match.group(2)
            following = next((x.strip() for x in lines[number + 1 :] if x.strip()), "")
            found.append(
                Heading(
                    text=self._key(raw.split("<")[0]),
                    repeats="<" in raw,
                    optional=following == "<!-- optional -->",
                )
            )
        return tuple(found)

    def missing(self, answer: str) -> list[str]:
        """The headings the answer lacks, or has out of order. Empty when it follows the
        template."""
        present = [self._key(m) for m in re.findall(r"^#{1,6}\s+(.*\S)\s*$", answer, re.MULTILINE)]
        problems: list[str] = []
        position = 0
        for heading in self.headings:
            at = self._find(present, heading, position)
            if at is None:
                if not heading.optional:
                    where = "is missing" if heading.text not in present else "is out of order"
                    problems.append(f"{heading.text!r} {where}")
                continue
            position = at + 1
            while (
                heading.repeats
                and position < len(present)
                and self._same(present[position], heading)
            ):
                position += 1  # every block of a repeating heading
        return problems

    @staticmethod
    def _same(found: str, heading: Heading) -> bool:
        return found.startswith(heading.text) if heading.repeats else found == heading.text

    def _find(self, present: list[str], heading: Heading, start: int) -> int | None:
        return next(
            (i for i in range(start, len(present)) if self._same(present[i], heading)), None
        )

    @staticmethod
    def _key(heading: str) -> str:
        """A heading without its placeholders, so `Option 2: Keep it` matches `Option 1: <name>`."""
        text = re.sub(r"<[^>]*>", "", heading)
        text = re.sub(r"\d+", "", text)
        return re.sub(r"\s+", " ", text).strip(" :").casefold()


class Prompts:
    """Finds the prompts and templates, and joins a role's prompt with the person's own guidance.

    prompts = Prompts()
    system = prompts.agent(Role.ANALYSIS)
    body = prompts.response("Feature")
    solution = prompts.artifact("solution")
    """

    def __init__(
        self, config: AgentsConfig | None = None, files_: ConfigFiles | None = None
    ) -> None:
        self._config = config or AgentsConfig.load()
        self._files = files_ or ConfigFiles()

    def agent(self, role: Role) -> str:
        """The instructions for a role: the general rules for every agent, the built-in prompt for
        the role, then the person's guidance if they set some. The first two are always there, and
        in this order."""
        builtin = f"{self.system()}\n\n{self._read(ROOT / 'agents' / f'{role.value}.md').rstrip()}"
        extra = getattr(self._config, role.value)
        if extra is None:
            return builtin + "\n"
        guidance = self._guidance(role, extra).strip()
        return (
            f"{builtin}\n\n## Additional guidance\n\n"
            "This guidance adds to the instructions above. It never replaces them.\n\n"
            f"<additional_guidance>\n{guidance}\n</additional_guidance>\n"
        )

    def system(self) -> str:
        """The rules of writing and communicating that every agent gets."""
        return self._read(ROOT / "system.md").rstrip()

    def response(self, issue_type: str) -> Template:
        """The ticket body template for an issue type."""
        name = re.sub(r"[^a-z0-9]+", "-", issue_type.lower()).strip("-")
        path = ROOT / "responses" / "ticket-format" / f"{name}.md"
        if not path.is_file():
            known = ", ".join(
                sorted(p.name[:-3] for p in (ROOT / "responses" / "ticket-format").iterdir())
            )
            raise ConfigError(
                f"there is no ticket body template for {issue_type!r}. Known: {known}"
            )
        return Template(name=f"response/{name}", text=self._read(path))

    def artifact(self, name: str) -> Template:
        """An artifact template, such as `solution`."""
        path = ROOT / "artifacts" / f"{name}.md"
        if not path.is_file():
            raise ConfigError(f"there is no artifact template {name!r}")
        return Template(name=f"artifact/{name}", text=self._read(path))

    # ----- inside -----

    @staticmethod
    def _read(path: Traversable) -> str:
        return path.read_text(encoding="utf-8")

    def _guidance(self, role: Role, path: Path) -> str:
        """The person's guidance file. A relative path is from the root of the repository."""
        resolved = path.expanduser()
        if not resolved.is_absolute():
            resolved = (self._files.root or self._files.start) / resolved
        if not resolved.is_file():
            raise ConfigError(
                f"the guidance file for agents.{role.value} does not exist: {resolved}"
            )
        return resolved.read_text(encoding="utf-8")
