import dataclasses
import logging
import os
import typing

import click.globals
import git
import rich
import rich.console
import rich.panel
import rich.status
import rich.text

from switchbox.repository import Config, Repository


class Status:
    def __init__(self, present: str, past: str, text: str) -> None:
        self.text = text
        self.present = present
        self.past = past
        self.status = rich.status.Status(
            status="{} {}".format(self.present, self.text),
            speed=2.0,
            spinner_style="arrow3",
        )

    def __enter__(self) -> "Status":
        self.status.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.status.__exit__(exc_type, exc_val, exc_tb)
        self.complete("{} {}.".format(self.past, self.text))

    @staticmethod
    def complete(task: str) -> None:
        rich.print("[green]✓[/]", task)


@dataclasses.dataclass()
class Context:
    repository: Repository

    @property
    def path(self) -> str:
        return f"[magenta]{self.repository.pretty_path}[/]"

    @property
    def mainline(self) -> str:
        return f"[cyan]{self.repository.mainline}[/]"

    @property
    def upstream(self) -> str:
        return f"[blue]{self.repository.upstream}[/]"

    @property
    def upstream_mainline(self) -> str:
        return f"{self.upstream}/{self.mainline}"

    def setup(self) -> None:
        self.set_mainline(self.repository.detect_mainline())
        self.set_upstream(self.repository.detect_upstream())

    def set_mainline(self, mainline: str) -> None:
        result = self.repository.set("mainline", mainline)
        Status.complete(
            f"Set [bold]{result.section}.{result.option}[/] = [cyan]{result.value}[/]."
        )

    def set_upstream(self, upstream: str) -> None:
        result = self.repository.set("upstream", upstream)
        Status.complete(
            f"Set [bold]{result.section}.{result.option}[/] = [blue]{result.value}[/]."
        )

    def update_remotes(self) -> None:
        with Status("Updating", "Updated", "all remotes"):
            self.repository.update_remotes()

    def update_mainline_branch(self) -> None:
        with Status(
            "Updating",
            "Updated",
            f"branch {self.mainline} to match {self.upstream_mainline}",
        ):
            self.repository.update_branch_from_remote(
                remote=self.repository.upstream,
                branch=self.repository.mainline,
            )

    def mainline_is_active_branch(self) -> bool:
        return self.repository.active_branch == self.mainline

    def switch_to_mainline_branch(self) -> None:
        with Status("Switching", "Switched", f"to the {self.mainline} branch"):
            self.repository.switch(self.repository.mainline)

    def remove_merged_branches(self) -> None:
        merged = self.repository.discover_merged_branches(self.repository.mainline)
        for branch in merged:
            with Status("Removing", "Removed", f"merged branch [cyan]{branch}[/]"):
                self.repository.remove_branch(branch)


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

    ctx.obj = Context(Repository(config=Config(), repo=git.Repo(path)))


@main.command()
@click.pass_obj
def setup(ctx: Context):
    """Detect and set 'switchbox.mainline' and 'switchbox.upstream'."""
    ctx.setup()


@main.command(name="update")
@click.pass_obj
def update_remotes(ctx: Context) -> None:
    """Run 'git remote update'."""
    ctx.update_remotes()


@main.command()
@click.pass_obj
def config(ctx: Context) -> None:
    """
    Display the git config options used by switchbox.

    You could also run 'git config --local --list | grep switchbox'.
    """
    for option in ctx.repository.options():
        print(option)


@main.command(name="set-mainline")
@click.argument("mainline", type=click.STRING)
@click.pass_obj
def set_mainline(ctx: Context, mainline: str) -> None:
    """Set 'switchbox.mainline' for this repository."""
    ctx.set_mainline(mainline)


@main.command(name="set-upstream")
@click.argument("upstream", type=click.STRING)
@click.pass_obj
def set_upstream(ctx: Context, upstream: str) -> None:
    """Set 'switchbox.upstream' for this repository."""
    ctx.set_upstream(upstream)


@main.command(name="end")
@click.pass_obj
def end(ctx: Context) -> None:
    """
    Finish working on a branch.

    Updates the mainline branch, switches to it, and deletes any merged branches.
    """
    ctx.update_remotes()
    if ctx.mainline_is_active_branch():
        raise click.ClickException("Already on the mainline branch")
    ctx.update_mainline_branch()
    ctx.switch_to_mainline_branch()
    ctx.remove_merged_branches()
