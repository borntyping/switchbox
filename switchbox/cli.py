import dataclasses
import logging
import os
import typing

import click.globals
import git
import inflect
import rich
import rich.console
import rich.panel
import rich.status
import rich.text
import rich.theme

from switchbox.repo import Config, Repo

p = inflect.engine()
console = rich.console.Console(
    theme=rich.theme.Theme(
        {
            "branch": "cyan",
            "remote": "blue",
        }
    )
)

remote_update_option = click.option(
    "--update/--no-update",
    "update_remotes",
    default=True,
    is_flag=True,
    help="Run 'git remote update --prune' before anything else.",
)
dry_run_option = click.option(
    "--dry-run/--no-dry-run",
    default=False,
    is_flag=True,
)


def plural(text: str, items: typing.Sized) -> str:
    return f"{len(items)} {p.plural(text, len(items))}"


def join(items: typing.Collection) -> str:
    return p.join(list(sorted(str(item) for item in items)))


@dataclasses.dataclass()
class Output:
    context: typing.Mapping[str, typing.Any]

    def __init__(self, **context: typing.Any) -> None:
        self.context = context

    def format(self, text: str) -> str:
        return text.format_map(self.context)

    def status(self, text: str) -> rich.status.Status:
        return rich.status.Status(self.format(text), speed=2.0, spinner="arrow3")

    def done(self, task: str) -> None:
        console.print("[green]✓[/]", self.format(task), highlight=False)

    def dry_run(self, task: str) -> None:
        console.print("[yellow]➔[/]", self.format(task), highlight=False)

    @classmethod
    def format_branch(cls, branch: str) -> str:
        return f"[branch]{branch}[/]"

    @classmethod
    def format_branches(cls, branches: typing.Collection[str]) -> str:
        return p.join([cls.format_branch(b) for b in sorted(branches)])

    @classmethod
    def format_remote(cls, branch: str) -> str:
        return f"[remote]{branch}[/]"


@dataclasses.dataclass()
class Application:
    repo: Repo

    def setup(self) -> None:
        self.set_mainline(self.repo.detect_mainline())
        self.set_upstream(self.repo.detect_upstream())

    def set_mainline(self, mainline: str) -> None:
        option = self.repo.set("mainline", mainline)
        Output(option=option).done("Set {option} = {option.value}.")

    def set_upstream(self, upstream: str) -> None:
        option = self.repo.set("upstream", upstream)
        Output(option=option).done("Set {option} = {option.value}.")

    def update_remotes(self) -> None:
        output = Output()
        with output.status("Updating all remotes..."):
            self.repo.update_remotes()
        output.done("Updated all remotes.")

    def update_mainline_branch(self) -> None:
        output = Output(
            mainline=f"[branch]{self.repo.mainline}[/]",
            upstream=f"[remote]{self.repo.upstream}[/]/[branch]{self.repo.mainline}[/]",
        )

        if self.repo.active_branch == self.repo.mainline:
            raise click.ClickException(
                output.format("Already on the {mainline} branch")
            )

        with output.status("Updating branch {mainline} to match {upstream}."):
            self.repo.update_branch_from_remote(
                remote=self.repo.upstream,
                branch=self.repo.mainline,
            )
        output.done("Updated branch {mainline} to match {upstream}.")

    def switch_to_mainline_branch(self) -> None:
        output = Output(mainline=f"[branch]{self.repo.mainline}[/]")
        with output.status("Switching to the {mainline} branch..."):
            self.repo.switch(self.repo.mainline)
        output.done("Switched to the {mainline} branch.")

    def remove_merged_branches(self, dry_run: bool = True) -> None:
        self._remove_branches(
            merged="[green]merged[/]",
            method=self.repo.discover_merged_branches,
            target=self.repo.mainline,
            dry_run=dry_run,
            force=False,
        )

    def remove_squashed_branches(self, dry_run: bool = True) -> None:
        self._remove_branches(
            merged="[magenta]squashed[/]",
            method=self.repo.discover_squashed_branches,
            target=self.repo.mainline,
            dry_run=dry_run,
            force=True,
        )

    def _remove_branches(
        self,
        merged: str,
        method: typing.Callable[[str], typing.Set[str]],
        target: str,
        dry_run: bool,
        force: bool,
    ) -> None:
        with Output(merged=merged).status("Finding {merged} branches..."):
            branches = method(target)

        output = Output(
            one=len(branches),
            branch=p.plural("branch", len(branches)),
            was=p.plural_verb("was", len(branches)),
            merged=merged,
            target=Output.format_branch(target),
            items=Output.format_branches(branches),
        )

        if not branches:
            output.done("There are no branches that have been {merged} into {target}.")
            return

        if dry_run:
            output.dry_run(
                "Found {one} {branch} "
                "that {was} {merged} into {target} "
                "and can be removed: {items}."
            )
            return

        with output.status("Removing {merged} {branch}..."):
            for branch in branches:
                self.repo.remove_branch(branch, force=force)

        output.done(
            "Found and removed {one} {branch} "
            "that {was} {merged} into {target}: {items}."
        )


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
    help="Will use GIT_DIR or the current directory if not set.",
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Show WARNING (-v), INFO (-vv), and DEBUG (-vvv) logs.",
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

    ctx.obj = Application(repo=Repo(config=Config(), gitpython=git.Repo(path)))


@main.group(invoke_without_command=True)
@click.pass_obj
@click.pass_context
def config(ctx: click.Context, app: Application) -> None:
    """
    Manage config options.

    Will display the git config options used by switchbox if no subcommand is given.
    """
    if ctx.invoked_subcommand is None:
        for option in app.repo.options():
            print(option)


@config.command()
@click.pass_obj
def config_init(app: Application):
    """
    Find and remember a mainline branch and upstream remote.

    This will be done automatically when you first use a command that works on a
    mainline branch or upstream remote.
    """
    app.setup()


@config.command(name="mainline")
@click.argument("mainline", type=click.STRING)
@click.pass_obj
def config_mainline(app: Application, mainline: str) -> None:
    """
    Set the mainline branch.

    Sets 'switchbox.mainline' in the repository's '.git/config' file.
    """
    app.set_mainline(mainline)


@config.command(name="upstream")
@click.argument("upstream", type=click.STRING)
@click.pass_obj
def config_upstream(app: Application, upstream: str) -> None:
    """
    Set the upstream remote.

    Sets 'switchbox.upstream' in the repository's '.git/config' file.
    """
    app.set_upstream(upstream)


@main.command()
@dry_run_option
@remote_update_option
@click.pass_obj
def finish(app: Application, dry_run: bool, update_remotes: bool) -> None:
    """
    Finish working on a branch.

    Updates the mainline branch, switches to it, and deletes any merged branches.
    """
    if update_remotes:
        app.update_remotes()
    app.update_mainline_branch()
    app.switch_to_mainline_branch()
    app.remove_merged_branches(dry_run=dry_run)
    app.remove_squashed_branches(dry_run=dry_run)


@main.command()
@dry_run_option
@remote_update_option
@click.pass_obj
def tidy(app: Application, dry_run: bool, update_remotes: bool) -> None:
    """
    Cleans up branches.

    Removes upstream branches that no longer exist and local branches that have been
    merged into the mainline branch.
    """
    if update_remotes:
        app.update_remotes()
    app.remove_merged_branches(dry_run=dry_run)
    app.remove_squashed_branches(dry_run=dry_run)


@main.command()
@click.pass_obj
def update(app: Application) -> None:
    """
    Update all git remotes.

    Just runs 'git remote update --prune'.
    """
    app.update_remotes()
