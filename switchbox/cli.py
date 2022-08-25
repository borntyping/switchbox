import logging
import os
import typing

import click.globals
import git
import rich
import rich.console
import rich.panel

from switchbox.repository import Config, Repository

pass_config = click.make_pass_decorator(Config)
dry_run_option = click.option("-n", "--dry-run", is_flag=True, help="Dry run.")


@click.group(name="switchbox")
@click.option(
    "--repo",
    "path",
    default=None,
    type=click.Path(
        dir_okay=True,
        file_okay=False,
        exists=True,
    ),
    help="Will use GIT_DIR or the current working directory if not set.",
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Show WARNING (-v), INFO (-vv), and DEBUG (-vvv) logging messages.",
)
@click.pass_context
def main(ctx: click.Context, path: typing.Optional[os.PathLike], verbose: int) -> None:
    verbosity = {
        0: logging.ERROR,
        1: logging.WARNING,
        2: logging.INFO,
        3: logging.DEBUG,
    }
    logging.basicConfig(level=verbosity[verbose])

    ctx.obj = Repository(
        config=Config(),
        repo=git.Repo(path),
        click=ctx,
    )


@main.command()
@click.pass_obj
def setup(repository: Repository):
    repository.update_remotes()
    repository.detect_mainline()


@main.command()
@click.pass_obj
def update(repository: Repository) -> None:
    repository.update_remotes()
    rich.print(f"Updated remotes for [blue]{repository}[/].")


@main.command()
@click.pass_obj
def status(repository: Repository) -> None:
    rich.print(
        rich.panel.Panel(
            renderable=rich.console.Group(
                f"Mainline branch is [cyan]{repository.mainline}[/].",
                f"Upstream remote is [cyan]{repository.upstream}[/].",
            ),
            title=f"[blue]{repository}[/]",
            width=80,
        )
    )


@main.command(name="set-mainline")
@click.argument("mainline", type=click.STRING)
@click.pass_obj
def set_mainline(repository: Repository, mainline: str) -> None:
    repository.set("mainline", mainline)
    repository.click.invoke(status)


@main.command(name="set-upstream")
@click.argument("upstream", type=click.STRING)
@click.pass_obj
def set_upstream(repository: Repository, upstream: str) -> None:
    repository.set("upstream", upstream)
    repository.click.invoke(status)


@main.command(name="end")
@click.pass_obj
def end(repository: Repository) -> None:
    repository.update_remotes()
    repository.click.invoke(status)

    assert repository.repo.head.name != repository.mainline
    repository.repo.git.fetch(
        repository.upstream,
        f"{repository.mainline}:{repository.mainline}",
        "--update-head-ok",
    )
    repository.repo.git.switch(repository.mainline)
