"""Pure parsing of the inline Question Analysis layout."""

import re
from dataclasses import dataclass

from fugue.github.parse import parse_body_sections


@dataclass(frozen=True)
class Option:
    number: int
    name: str
    you_get: str
    changes: str
    watch_out: str
    scope_name: str
    scope_url: str
    affected_areas: str = ""


@dataclass(frozen=True)
class Analysis:
    text: str
    overview: str
    options: tuple[Option, ...]
    recommendation: str

    def option(self, number: int) -> Option:
        for option in self.options:
            if option.number == number:
                return option
        raise ValueError(f"Unknown option {number}")


SCOPE_LINK = re.compile(
    r"\*\*(?:Codex scope|Implementation scope):?\*\*:?\s*"
    r"(?P<marker>\[Selected Solution\] )?"
    r"\[(?P<name>(?:\[Selected Solution\] )?[^\n]+?)\]\((?P<url>[^\s)]+)\)"
)


def prose_only(text: str) -> str:
    """Mask fenced examples while retaining offsets into the original Markdown."""
    lines: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if fence_character is not None:
            if re.fullmatch(
                r" {0,3}" + re.escape(fence_character) + r"{" + str(fence_length) + r",}[ \t]*",
                content,
            ):
                fence_character = None
            lines.append(" " * len(content) + line[len(content) :])
            continue
        fence = re.fullmatch(r" {0,3}(`{3,}|~{3,})(.*)", content)
        if fence and (fence[1][0] != "`" or "`" not in fence[2]):
            fence_character = fence[1][0]
            fence_length = len(fence[1])
            lines.append(" " * len(content) + line[len(content) :])
        else:
            lines.append(line)
    return "".join(lines)


def _field(block: str, label: str) -> str:
    labels = list(re.finditer(r"^\*\*(.+?):?\*\*:?[ \t]*", prose_only(block), re.MULTILINE))
    for index, match in enumerate(labels):
        if match[1].removesuffix(":") == label:
            end = labels[index + 1].start() if index + 1 < len(labels) else len(block)
            return block[match.end() : end].strip()
    return ""


def parse_analysis(body: str) -> Analysis:
    """Read only Analysis; incomplete options remain browsable but cannot be selected."""
    text = parse_body_sections(body).get("Analysis", "")
    headings = list(re.finditer(r"^###[ \t]+(.+?)[ \t]*$", prose_only(text), re.MULTILINE))
    options: list[Option] = []
    recommendation = ""
    first_option = len(text)
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        block = text[heading.end() : end].strip()
        if heading[1] == "Recommendation":
            recommendation = block.split("> [!TIP]", 1)[0].strip()
        match = re.fullmatch(r"Option ([1-9][0-9]*):\s*(.+)", heading[1])
        if match is None:
            continue
        first_option = min(first_option, heading.start())
        number = int(match[1])
        if any(option.number == number for option in options):
            raise ValueError(f"Duplicate option {number}")
        link = SCOPE_LINK.search(prose_only(block))
        options.append(
            Option(
                number,
                match[2],
                _field(block, "You get"),
                _field(block, "Changes"),
                _field(block, "Watch out"),
                link["name"].removeprefix("[Selected Solution] ") if link else "",
                link["url"] if link else "",
                _field(block, "Affected areas"),
            )
        )
    return Analysis(text, text[:first_option].strip(), tuple(options), recommendation)
