from pathlib import Path

import pytest
from pydantic import ValidationError

from fugue.config import ConfigError, FugueConfig, RoleConfig, load_config
from fugue.models import AuthenticationMode, Role, Size


def write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "fugue.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_defaults() -> None:
    config = load_config(None)
    assert config == FugueConfig()
    assert config.state_dir is None
    assert config.policy.autonomy_profile == "human"
    assert config.policy.commit_subject_max == 72
    assert config.policy.gate_commands == [
        ["uv", "run", "ruff", "format", "--check", "."],
        ["uv", "run", "ruff", "check", "."],
        ["uv", "run", "mypy"],
        ["uv", "run", "pytest"],
        ["uv", "build"],
    ]
    assert config.limits.model_dump() == {
        "max_repair_attempts": 3,
        "max_concurrent_runs": 1,
        "agent_timeout_seconds": 3600,
        "max_output_bytes": 65536,
    }
    assert {role: settings.provider for role, settings in config.roles.items()} == {
        Role.ANALYST: "claude",
        Role.IMPLEMENTER: "codex",
        Role.REVIEWER: "codex",
    }
    for settings in config.roles.values():
        assert settings.model is None
        assert settings.authentication_mode is AuthenticationMode.SUBSCRIPTION_LOGIN


def test_empty_toml_uses_defaults(tmp_path: Path) -> None:
    assert load_config(write_config(tmp_path, "")) == load_config(None)


def test_toml_overrides(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
state_dir = "local-state"
[policy]
autonomy_profile = "campaign"
commit_subject_max = 60
gate_commands = [["uv", "run", "pytest"]]
[policy.size_rules.S]
analysis = true
review = false
max_components = 2
[limits]
max_repair_attempts = 5
max_concurrent_runs = 2
agent_timeout_seconds = 60
max_output_bytes = 1024
[roles.analyst]
provider = "codex"
model = "chosen-model"
""",
    )
    config = load_config(path)
    assert config.state_dir == Path("local-state")
    assert config.policy.autonomy_profile == "campaign"
    assert config.policy.commit_subject_max == 60
    assert config.policy.gate_commands == [["uv", "run", "pytest"]]
    assert config.policy.size_rules[Size.S].model_dump() == {
        "analysis": True,
        "review": False,
        "max_components": 2,
    }
    assert config.limits.model_dump() == {
        "max_repair_attempts": 5,
        "max_concurrent_runs": 2,
        "agent_timeout_seconds": 60,
        "max_output_bytes": 1024,
    }
    assert config.roles[Role.ANALYST].provider == "codex"
    assert config.roles[Role.ANALYST].model == "chosen-model"
    assert config.roles[Role.ANALYST].authentication_mode is AuthenticationMode.SUBSCRIPTION_LOGIN


def test_reviewer_override_keeps_other_role_defaults(tmp_path: Path) -> None:
    config = load_config(
        write_config(tmp_path, '[roles.reviewer]\nprovider = "claude"\nmodel = "review-model"')
    )
    defaults = load_config(None)
    assert set(config.roles) == set(Role)
    assert config.roles[Role.REVIEWER].provider == "claude"
    assert config.roles[Role.REVIEWER].model == "review-model"
    for role in (Role.ANALYST, Role.IMPLEMENTER):
        assert config.roles[role] == defaults.roles[role]


def test_size_rule_override_keeps_other_size_defaults(tmp_path: Path) -> None:
    config = load_config(
        write_config(
            tmp_path,
            "[policy.size_rules.S]\nanalysis = true\nreview = false\nmax_components = 3",
        )
    )
    defaults = load_config(None)
    assert set(config.policy.size_rules) == set(Size)
    assert config.policy.size_rules[Size.S].model_dump() == {
        "analysis": True,
        "review": False,
        "max_components": 3,
    }
    for size in (Size.XS, Size.M, Size.L, Size.XL):
        assert config.policy.size_rules[size] == defaults.policy.size_rules[size]


@pytest.mark.parametrize(
    ("content", "key"),
    [
        ('[roles.reviewer]\nmodel = "review-model"', "roles.reviewer.provider"),
        ("[policy.size_rules.S]\nanalysis = true", "policy.size_rules.S.review"),
    ],
)
def test_overridden_entries_replace_the_entire_entry(
    tmp_path: Path, content: str, key: str
) -> None:
    with pytest.raises(ConfigError) as error:
        load_config(write_config(tmp_path, content))
    assert key in str(error.value)


@pytest.mark.parametrize(
    ("content", "key"),
    [
        ("unknown = true", "unknown"),
        ("[policy]\nunknown = true", "policy.unknown"),
        ("[limits]\nunknown = true", "limits.unknown"),
        (
            '[roles.analyst]\nprovider = "claude"\nunknown = true',
            "roles.analyst.unknown",
        ),
        (
            "[policy.size_rules.S]\nanalysis = false\nreview = true\n"
            "max_components = 1\nunknown = true",
            "policy.size_rules.S.unknown",
        ),
        ('[roles.unknown]\nprovider = "claude"', "roles.unknown"),
        ('[roles.analyst]\nprovider = "unknown"', "roles.analyst.provider"),
        (
            '[roles.analyst]\nprovider = "claude"\nauthentication_mode = "unknown"',
            "roles.analyst.authentication_mode",
        ),
        ('[policy]\nautonomy_profile = "unknown"', "policy.autonomy_profile"),
        (
            "[policy.size_rules.XXL]\nanalysis = false\nreview = true\nmax_components = 1",
            "policy.size_rules.XXL",
        ),
    ],
)
def test_invalid_config_names_dotted_key(tmp_path: Path, content: str, key: str) -> None:
    with pytest.raises(ConfigError) as error:
        load_config(write_config(tmp_path, content))
    assert key in str(error.value)


def test_api_key_requires_explicit_toml(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="authentication_mode"):
        RoleConfig(provider="codex", authentication_mode=AuthenticationMode.API_KEY)
    with pytest.raises(ValidationError, match=r"roles\.implementer\.authentication_mode"):
        FugueConfig.model_validate(
            {"roles": {"implementer": {"provider": "codex", "authentication_mode": "api_key"}}}
        )
    path = write_config(
        tmp_path,
        '[roles.implementer]\nprovider = "codex"\nauthentication_mode = "api_key"',
    )
    config = load_config(path)
    assert config.roles[Role.IMPLEMENTER].authentication_mode is AuthenticationMode.API_KEY
    assert load_config(None).roles[Role.IMPLEMENTER].authentication_mode is (
        AuthenticationMode.SUBSCRIPTION_LOGIN
    )


@pytest.mark.parametrize(
    "key",
    ["max_repair_attempts", "max_concurrent_runs", "agent_timeout_seconds", "max_output_bytes"],
)
@pytest.mark.parametrize("value", [-1, 0])
def test_limits_reject_nonpositive_values(tmp_path: Path, key: str, value: int) -> None:
    with pytest.raises(ConfigError, match=rf"limits\.{key}"):
        load_config(write_config(tmp_path, f"[limits]\n{key} = {value}"))


def test_positive_limit_boundaries(tmp_path: Path) -> None:
    config = load_config(
        write_config(
            tmp_path,
            "[limits]\nmax_repair_attempts = 1\nmax_concurrent_runs = 1\n"
            "agent_timeout_seconds = 1\nmax_output_bytes = 1",
        )
    )
    assert set(config.limits.model_dump().values()) == {1}


def test_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "missing.toml"
    with pytest.raises(ConfigError) as error:
        load_config(path)
    assert str(path) in str(error.value)


def test_invalid_toml(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[invalid")
    with pytest.raises(ConfigError) as error:
        load_config(path)
    assert str(path) in str(error.value)


def test_invalid_utf8_is_config_error(tmp_path: Path) -> None:
    path = tmp_path / "invalid-utf8.toml"
    path.write_bytes(b' state_dir = "\xff"')
    with pytest.raises(ConfigError) as error:
        load_config(path)
    assert str(path) in str(error.value)
    assert isinstance(error.value.__cause__, UnicodeDecodeError)


def test_directory_is_not_a_config_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_mutable_defaults_are_independent() -> None:
    first = load_config(None)
    second = load_config(None)
    first.policy.size_rules[Size.XS].max_components = 9
    first.policy.gate_commands[0].append("changed")
    first.roles[Role.ANALYST].model = "changed"
    assert second.policy.size_rules[Size.XS].max_components == 1
    assert second.policy.gate_commands[0] == ["uv", "run", "ruff", "format", "--check", "."]
    assert second.roles[Role.ANALYST].model is None


def test_github_and_worktrees_defaults() -> None:
    config = load_config(None)
    assert config.github.repo is None
    assert config.github.project_owner is None
    assert config.github.project_number is None
    assert config.worktrees_root is None


def test_github_and_worktrees_from_toml(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        'worktrees_root = "/srv/trees"\n'
        "[github]\n"
        'repo = "owner/name"\n'
        'project_owner = "owner"\n'
        "project_number = 7\n",
    )
    config = load_config(path)
    assert config.github.repo == "owner/name"
    assert config.github.project_owner == "owner"
    assert config.github.project_number == 7
    assert config.worktrees_root == Path("/srv/trees")


def test_github_rejects_unknown_key_and_bad_number(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as unknown:
        load_config(write_config(tmp_path, "[github]\ntoken = 'x'\n"))
    assert "github.token" in str(unknown.value)
    with pytest.raises(ConfigError) as number:
        load_config(write_config(tmp_path, "[github]\nproject_number = 0\n"))
    assert "github.project_number" in str(number.value)
