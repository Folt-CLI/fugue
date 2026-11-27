"""`fugue config`: see where settings come from, and change them."""

from typing import Annotated

import typer

from fugue.cli.views import ConfigView, Messages
from fugue.configfiles import Scope
from fugue.core.settings import Settings

app = typer.Typer(help="See and change Fugue's settings.", no_args_is_help=True)

Key = Annotated[str, typer.Argument(help="A setting, such as github.project.")]
Global = Annotated[
    bool, typer.Option("--global", help="For every repository on this machine (~/.config/fugue).")
]
Private = Annotated[
    bool, typer.Option("--private", help="Yours alone, in .fugue/config.local.toml (git-ignored).")
]


def _scope(global_: bool, private: bool) -> Scope:
    """`--global`, `--private`, or by default the repository's own `.fugue/config.toml`."""
    if global_ and private:
        raise typer.BadParameter("choose one of --global and --private")
    return Scope.GLOBAL if global_ else Scope.PRIVATE if private else Scope.LOCAL


@app.command("list")
def list_() -> None:
    """Every setting, its value, and which file or variable it comes from."""
    messages = Messages()
    with messages.guard():
        settings = Settings()
        files = [
            (scope.value, settings.short(path), path.is_file())
            for scope in Scope.order()
            if (path := settings.files.path(scope)) is not None
        ]
        view = ConfigView()
        view.table(settings.all(), files)
        global_file = settings.files.path(Scope.GLOBAL)
        view.ignored(settings.ignored(), settings.short(global_file))


@app.command()
def get(key: Key) -> None:
    """Print one setting's value, and nothing else, so a script can use it."""
    messages = Messages()
    with messages.guard():
        setting = Settings().get(key)
    if setting.value is None:
        raise typer.Exit(1)
    typer.echo("set" if setting.secret else setting.value)


@app.command()
def set(
    key: Key,
    value: Annotated[str, typer.Argument(help="The new value.")],
    global_: Global = False,
    private: Private = False,
) -> None:
    """Write a setting. By default it goes in the repository's .fugue/config.toml."""
    messages = Messages()
    with messages.guard():
        settings = Settings()
        scope = _scope(global_, private)
        path = settings.set(key, value, scope)
        messages.success(f"{key} = {value}  [dim]({scope} · {settings.short(path)})[/]")
        if scope is Scope.PRIVATE and not settings.is_ignored(path):
            messages.warning(f"{path.name} is not git-ignored: add .fugue/config.local.toml")


@app.command()
def unset(key: Key, global_: Global = False, private: Private = False) -> None:
    """Remove a setting from a file, so the next layer down applies."""
    messages = Messages()
    with messages.guard():
        scope = _scope(global_, private)
        settings = Settings()
        path = settings.unset(key, scope)
        messages.success(f"{key} removed  [dim]({scope} · {settings.short(path)})[/]")
