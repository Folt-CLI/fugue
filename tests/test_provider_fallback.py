import asyncio
import tomllib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from fugue.agents import (
    AccessMode,
    AdapterRegistry,
    AgentRequest,
    AgentResult,
    Capability,
    ProviderAdapter,
    ProviderSwitch,
    ScriptedAdapter,
    ScriptStep,
    Unavailable,
)
from fugue.cli.render import render_config, render_roles
from fugue.config import ConfigError, FugueConfig, ProviderEntry, RoleConfig, load_config
from fugue.models import AuthenticationMode, Role


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def outcome(
    name: str,
    *,
    ok: bool = False,
    unavailable: Unavailable | None = None,
    timed_out: bool = False,
    cancelled: bool = False,
    session_id: str | None = None,
) -> AgentResult:
    return AgentResult(
        ok=ok,
        final_text="done" if ok else "",
        structured_output=None,
        session_id=session_id,
        usage=None,
        provider=name,
        authentication_mode=AuthenticationMode.SUBSCRIPTION_LOGIN,
        error=None if ok else f"{name} failed",
        timed_out=timed_out,
        cancelled=cancelled,
        log_path=None,
        unsupported=None,
        provider_limited=unavailable == Unavailable.RATE_LIMITED,
        unavailable=unavailable,
    )


def script(
    name: str,
    *results: AgentResult,
    capabilities: frozenset[Capability] = frozenset(Capability),
) -> ScriptedAdapter:
    return ScriptedAdapter(
        [ScriptStep(result=item) for item in results], name=name, capabilities=capabilities
    )


def provider(name: str) -> Literal["claude", "codex"]:
    assert name in {"claude", "codex"}
    return cast(Literal["claude", "codex"], name)


def factory(adapter: ScriptedAdapter) -> Callable[[RoleConfig], ProviderAdapter]:
    return lambda config: adapter


class Chain:
    def __init__(self, primary: ScriptedAdapter, *others: ScriptedAdapter) -> None:
        self.registry = AdapterRegistry()
        for adapter in (primary, *others):
            self.registry.register(adapter.name, factory(adapter))
        self.clock = Clock()
        self.switches: list[ProviderSwitch] = []
        self.role = RoleConfig(
            provider=provider(primary.name),
            fallback=[
                ProviderEntry(provider=provider(o.name), model=f"{o.name}-model") for o in others
            ],
        )

    def build(self) -> ProviderAdapter:
        return self.registry.build_for_role(
            self.role,
            clock=self.clock,
            cooldown_seconds=300,
            on_switch=self.switches.append,
        )


def request(
    tmp_path: Path,
    *,
    access: AccessMode = AccessMode.WORKSPACE_WRITE,
    output_schema: Mapping[str, Any] | None = None,
    sessions: Mapping[str, str] | None = None,
    fresh_prompt: str | None = None,
) -> AgentRequest:
    return AgentRequest(
        Role.IMPLEMENTER,
        "repair prompt",
        tmp_path,
        access,
        output_schema=output_schema,
        sessions=sessions or {},
        fresh_prompt=fresh_prompt,
    )


def run(chain: Chain, req: AgentRequest) -> AgentResult:
    return asyncio.run(chain.build().run(req))


# Scenarios 1 and 2: configuration.


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "fugue.toml"
    path.write_text(text)
    return path


def test_default_roles_have_no_fallback_and_render_round_trips(tmp_path: Path) -> None:
    config = FugueConfig()
    assert all(not role.fallback for role in config.roles.values())
    # render_config omits unset values (the L size rule's max_components), so check that
    # the output is valid TOML and that the roles survive it.
    parsed = tomllib.loads(render_config(config))
    assert {name: RoleConfig.model_validate(item) for name, item in parsed["roles"].items()} == {
        str(role): settings for role, settings in config.roles.items()
    }


def test_fallback_loads_renders_and_round_trips(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[roles.implementer]\nprovider = "codex"\n'
        '[[roles.implementer.fallback]]\nprovider = "claude"\nmodel = "m"\n',
    )
    config = load_config(path)
    entry = config.roles[Role.IMPLEMENTER].fallback[0]
    assert (entry.provider, entry.model) == ("claude", "m")
    rendered = render_config(config)
    assert "[[roles.implementer.fallback]]" in rendered
    parsed = tomllib.loads(rendered)
    assert (
        RoleConfig.model_validate(parsed["roles"]["implementer"]) == config.roles[Role.IMPLEMENTER]
    )
    assert "codex > claude" in render_roles(config)


@pytest.mark.parametrize(
    "fallback",
    [
        'provider = "claude"\nauthentication_mode = "api_key"',
        'provider = ""',
        'provider = "other"',
        'provider = "codex"',
    ],
)
def test_invalid_fallback_entries(tmp_path: Path, fallback: str) -> None:
    path = write(
        tmp_path,
        f'[roles.implementer]\nprovider = "codex"\n[[roles.implementer.fallback]]\n{fallback}\n',
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_repeated_fallback_provider_and_cooldown_bounds(tmp_path: Path) -> None:
    repeated = (
        '[roles.reviewer]\nprovider = "codex"\n'
        '[[roles.reviewer.fallback]]\nprovider = "claude"\n'
        '[[roles.reviewer.fallback]]\nprovider = "claude"\n'
    )
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, repeated))
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, "[limits]\nprovider_cooldown_seconds = 0\n"))


# Scenarios 3 to 5: unavailable results switch to the next provider.


@pytest.mark.parametrize("reason", list(Unavailable))
def test_unavailable_primary_switches(tmp_path: Path, reason: Unavailable) -> None:
    primary = script("codex", outcome("codex", unavailable=reason))
    backup = script("claude", outcome("claude", ok=True, session_id="s"))
    chain = Chain(primary, backup)
    result = run(chain, request(tmp_path))
    assert result.ok and result.provider == "claude"
    assert [(s.from_provider, s.to_provider, s.reason) for s in chain.switches] == [
        ("codex", "claude", reason.value)
    ]
    assert chain.switches[0].authentication_mode == AuthenticationMode.SUBSCRIPTION_LOGIN
    assert backup.requests[0].model == "claude-model"


# Scenario 6: capability skips.


def test_capability_skip_without_call(tmp_path: Path) -> None:
    primary = script("codex", capabilities=frozenset(Capability) - {Capability.STRUCTURED_OUTPUT})
    backup = script("claude", outcome("claude", ok=True))
    chain = Chain(primary, backup)
    result = run(chain, request(tmp_path, output_schema={"type": "object"}))
    assert result.provider == "claude" and primary.requests == []
    assert chain.switches[0].reason == "capability:structured_output"


def test_no_switch_reported_to_skipped_member(tmp_path: Path) -> None:
    first = script("codex", outcome("codex", unavailable=Unavailable.RATE_LIMITED))
    limited = ScriptedAdapter(
        [], name="claude", capabilities=frozenset(Capability) - {Capability.READ_ONLY_MODE}
    )
    chain = Chain(first, limited)
    result = run(chain, request(tmp_path, access=AccessMode.READ_ONLY))
    assert result.provider_limited and limited.requests == []
    assert chain.switches == []


def test_all_members_lack_capability(tmp_path: Path) -> None:
    only = frozenset(Capability) - {Capability.READ_ONLY_MODE}
    chain = Chain(
        ScriptedAdapter([], name="codex", capabilities=only),
        ScriptedAdapter([], name="claude", capabilities=only),
    )
    result = run(chain, request(tmp_path, access=AccessMode.READ_ONLY))
    assert not result.ok and not result.provider_limited
    assert result.unsupported == Capability.READ_ONLY_MODE


# Scenario 7: ordinary failures never switch.


@pytest.mark.parametrize(
    "failure",
    [
        outcome("codex"),
        outcome("codex", timed_out=True),
        outcome("codex", cancelled=True),
    ],
)
def test_ordinary_failure_returns_unchanged(tmp_path: Path, failure: AgentResult) -> None:
    primary = script("codex", failure)
    backup = script("claude")
    chain = Chain(primary, backup)
    assert run(chain, request(tmp_path)) is failure
    assert backup.requests == [] and chain.switches == []


# Scenario 8: sessions and prompts.


def test_session_and_prompt_per_member(tmp_path: Path) -> None:
    primary = script("codex", outcome("codex", unavailable=Unavailable.RATE_LIMITED))
    backup = script("claude", outcome("claude", ok=True))
    chain = Chain(primary, backup)
    run(
        chain,
        request(tmp_path, sessions={"codex": "codex-session"}, fresh_prompt="scope + repair"),
    )
    assert (primary.requests[0].session_id, primary.requests[0].prompt) == (
        "codex-session",
        "repair prompt",
    )
    fallback = backup.requests[0]
    assert fallback.session_id is None
    assert fallback.prompt.startswith("scope + repair")
    assert "could not complete this call" in fallback.prompt
    assert dict(fallback.sessions) == {}


def test_fallback_resumes_its_own_session(tmp_path: Path) -> None:
    primary = script("codex", outcome("codex", unavailable=Unavailable.NOT_SIGNED_IN))
    backup = script("claude", outcome("claude", ok=True))
    chain = Chain(primary, backup)
    run(chain, request(tmp_path, sessions={"claude": "claude-session"}, fresh_prompt="fresh"))
    assert backup.requests[0].session_id == "claude-session"
    assert backup.requests[0].prompt.startswith("repair prompt")


# Scenario 10 and 14: cool-down.


def test_cooldown_skips_then_retries_after_interval(tmp_path: Path) -> None:
    primary = script(
        "codex",
        outcome("codex", unavailable=Unavailable.RATE_LIMITED),
        outcome("codex", ok=True),
    )
    backup = script("claude", outcome("claude", ok=True), outcome("claude", ok=True))
    chain = Chain(primary, backup)
    run(chain, request(tmp_path))
    assert len(primary.requests) == 1
    chain.clock.advance(100)
    assert run(chain, request(tmp_path)).provider == "claude"
    assert len(primary.requests) == 1
    chain.clock.advance(201)
    assert run(chain, request(tmp_path)).provider == "codex"


def test_cooled_primary_retried_when_later_member_unavailable(tmp_path: Path) -> None:
    primary = script(
        "codex",
        outcome("codex", unavailable=Unavailable.RATE_LIMITED),
        outcome("codex", ok=True),
    )
    backup = script(
        "claude",
        outcome("claude", ok=True),
        outcome("claude", unavailable=Unavailable.UNREACHABLE),
    )
    chain = Chain(primary, backup)
    run(chain, request(tmp_path))
    chain.clock.advance(10)
    result = run(chain, request(tmp_path))
    assert result.ok and result.provider == "codex"
    assert len(primary.requests) == 2


def test_all_cooling_tried_in_order(tmp_path: Path) -> None:
    primary = script(
        "codex",
        outcome("codex", unavailable=Unavailable.RATE_LIMITED),
        outcome("codex", ok=True),
    )
    backup = script("claude", outcome("claude", unavailable=Unavailable.RATE_LIMITED))
    chain = Chain(primary, backup)
    assert run(chain, request(tmp_path)).provider_limited
    chain.clock.advance(1)
    result = run(chain, request(tmp_path))
    assert result.ok and result.provider == "codex"


def test_cooldown_shared_between_runs_in_one_process(tmp_path: Path) -> None:
    primary = script("codex", outcome("codex", unavailable=Unavailable.RATE_LIMITED))
    backup = script("claude", outcome("claude", ok=True), outcome("claude", ok=True))
    chain = Chain(primary, backup)
    run(chain, request(tmp_path))
    other_run = chain.build()
    assert asyncio.run(other_run.run(request(tmp_path))).provider == "claude"
    assert len(primary.requests) == 1


# Scenario 11: every member unavailable.


def test_all_unavailable_is_provider_limited(tmp_path: Path) -> None:
    chain = Chain(
        script("codex", outcome("codex", unavailable=Unavailable.RATE_LIMITED)),
        script("claude", outcome("claude", unavailable=Unavailable.NOT_SIGNED_IN)),
    )
    result = run(chain, request(tmp_path))
    assert not result.ok and result.provider_limited
    assert result.error is not None
    assert "codex: rate_limited" in result.error
    assert "claude: not_signed_in" in result.error
