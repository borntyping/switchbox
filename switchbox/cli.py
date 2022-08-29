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
import inflect

from switchbox.repository import Config, GitOption, Repository

p = inflect.engine()

remote_update_option = click.option(
    "--update/--no-update",
    default=True,
    help="Run 'git remote update --prune' before anything else.",
)


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
        self._set(self.repository.set("mainline", mainline))

    def set_upstream(self, upstream: str) -> None:
        self._set(self.repository.set("upstream", upstream))

    def _set(self, result: GitOption) -> None:
        option = f"[bold]{result.section}.{result.option}[/]"
        value = f"[blue]{result.value}[/]"
        self.done(f"Set {option} = {value}.")

    def update_remotes(self) -> None:
        with rich.status.Status("Updating all remotes..."):
            self.repository.update_remotes()
        self.done("Updated all remotes.")

    def update_mainline_branch(self) -> None:
        with rich.status.Status(
            f"Updating branch {self.mainline} to match {self.upstream_mainline}."
        ):
            self.repository.update_branch_from_remote(
                remote=self.repository.upstream,
                branch=self.repository.mainline,
            )
        self.done(f"Updated branch {self.mainline} to match {self.upstream_mainline}.")

    def mainline_is_active_branch(self) -> bool:
        return self.repository.active_branch == self.mainline

    def switch_to_mainline_branch(self) -> None:
        with rich.status.Status(f"Switching to the {self.mainline} branch..."):
            self.repository.switch(self.repository.mainline)
        self.done(f"Switched to the {self.mainline} branch.")

    def remove_merged_branches(self) -> None:
        with rich.status.Status("Finding merged branches..."):
            merged = self.repository.discover_merged_branches(self.repository.mainline)

        if not merged:
            self.done(f"No merged branches to cleanup.")
            return

        self.done(f"Found {len(merged)} {p.plural('branch', len(merged))}.")
        for branch in merged:
            with rich.status.Status("Removing merged branch [cyan]{branch}[/]..."):
                self.repository.remove_branch(branch)
            self.done("Removed merged branch [cyan]{branch}[/].")

    @staticmethod
    def done(task: str) -> None:
        rich.print("[green]✓[/]", task)


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


@main.command(name="tidy")
@remote_update_option
@click.pass_obj
def tidy(ctx: Context, update: bool) -> None:
    """
    Cleans up branches.

    Removes upstream branches that no longer exist and local branches that have been
    merged into the mainline branch.
    """
    if update:
        ctx.update_remotes()
    ctx.remove_merged_branches()


@main.command(name="end")
@remote_update_option
@click.pass_obj
def end(ctx: Context, update: bool) -> None:
    """
    Finish working on a branch.

    Updates the mainline branch, switches to it, and deletes any merged branches.
    """
    if update:
        ctx.update_remotes()
    if ctx.mainline_is_active_branch():
        raise click.ClickException("Already on the mainline branch")
    ctx.update_mainline_branch()
    ctx.switch_to_mainline_branch()
    ctx.remove_merged_branches()
