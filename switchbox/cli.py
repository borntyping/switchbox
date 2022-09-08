import collections
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


OutputContextValue = typing.Union[typing.Callable[[], typing.Any], typing.Any]


class OutputContext(collections.UserDict[str, OutputContextValue]):
    def __getitem__(self, item: str) -> str:
        value = super().__getitem__(item)
        return value() if callable(value) else value


@dataclasses.dataclass()
class Output:
    context: OutputContext

    def __init__(self, **context: OutputContextValue) -> None:
        self.context = OutputContext(context)

    def format(self, text: str) -> str:
        return text.format_map(self.context)

    def status(self, text: str) -> rich.status.Status:
        return rich.status.Status(self.format(text), speed=2.0)

    def done(self, task: str) -> None:
        console.print("[green]✓[/]", self.format(task), highlight=False)

    def dry_run(self, task: str) -> None:
        console.print("[yellow]➔[/]", self.format(task), highlight=False)

    @classmethod
    def format_branch(cls, branch: str | None) -> str:
        return f"[branch]{branch}[/]" if branch else "[red]UNSET[/]"

    @classmethod
    def format_branches(cls, branches: typing.Collection[str]) -> str:
        return p.join([f"[branch]{branch}[/]" for branch in sorted(branches)])

    @classmethod
    def format_remote(cls, remote: str | None) -> str:
        return f"[remote]{remote}[/]" if remote else "[red]UNSET[/]"


@dataclasses.dataclass()
class Application:
    repo: Repo

    def init(self) -> None:
        self.set_default_branch(self.repo.detect_default_branch())
        self.set_default_remote(self.repo.detect_default_remote())
        self.remove_option("upstream")
        self.remove_option("mainline")

    @property
    def context(self) -> OutputContext:
        return OutputContext(
            active_branch=lambda: Output.format_branch(self.repo.active_branch),
            default_branch=lambda: Output.format_branch(self.repo.default_branch),
            default_remote=lambda: Output.format_remote(self.repo.default_remote),
            default_remote_branch=lambda: "{}/{}".format(
                Output.format_branch(self.repo.default_branch),
                Output.format_remote(self.repo.default_remote),
            ),
        )

    def set_default_branch(self, branch: str) -> None:
        option = self.repo.set("default-branch", branch)
        Output(option=option).done("Set {option} = {option.value}.")

    def set_default_remote(self, remote: str) -> None:
        option = self.repo.set("default-remote", remote)
        Output(option=option).done("Set {option} = {option.value}.")

    def remove_option(self, option: str) -> None:
        output = Output(option=option)
        if self.repo.remove_option(option):
            output.done("Removed option {option}.")

    def update_remotes(self) -> None:
        output = Output()
        with output.status("Updating all remotes..."):
            self.repo.update_remotes()
        output.done("Updated all remotes.")

    def update_default_branch(self) -> None:
        output = Output(**self.context)

        if self.repo.active_branch == self.repo.default_branch:
            raise click.ClickException(f"Already on branch {self.repo.default_branch}")

        with output.status(
            "Updating branch {default_branch} "
            "to match {default_branch}/{default_remote}."
        ):
            self.repo.update_branch_from_remote(
                remote=self.repo.default_remote,
                branch=self.repo.default_branch,
            )
        output.done(
            "Updated branch {default_branch} "
            "to match {default_branch}/{default_remote}."
        )

    def switch_default_branch(self) -> None:
        output = Output(**self.context)
        with output.status("Switching to the {default_branch} branch..."):
            self.repo.switch(self.repo.default_branch)
        output.done("Switched to the {default_branch} branch.")

    def remove_merged_branches(self, dry_run: bool = True) -> None:
        self._remove_branches(
            merged="[green]merged[/]",
            method=self.repo.discover_merged_branches,
            dry_run=dry_run,
            force=False,
        )

    def remove_rebased_branches(self, dry_run: bool = True) -> None:
        self._remove_branches(
            merged="[yellow]rebased[/]",
            method=self.repo.discover_rebased_branches,
            dry_run=dry_run,
            force=True,
        )

    def remove_squashed_branches(self, dry_run: bool = True) -> None:
        self._remove_branches(
            merged="[magenta]squashed[/]",
            method=self.repo.discover_squashed_branches,
            dry_run=dry_run,
            force=True,
        )

    def _remove_branches(
        self,
        merged: str,
        method: typing.Callable[[str], typing.Iterable[str]],
        dry_run: bool,
        force: bool,
    ) -> None:
        default_branch = self.repo.default_branch

        with Output(merged=merged).status("Finding {merged} branches..."):
            branches = set(method(default_branch))

        output = Output(
            one=len(branches),
            branch=p.plural("branch", len(branches)),
            was=p.plural_verb("was", len(branches)),
            merged=merged,
            target=Output.format_branch(default_branch),
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

    def rebase_active_branch(self) -> typing.Tuple[str, str]:
        """Rebase the active branch on top of the remote default branch."""
        output = Output(**self.context)
        with output.status("Rebasing onto {default_branch}/{default_remote}..."):
            before = self.repo.active_branch_ref()
            self.repo.rebase(upstream=self.repo.remote_default_branch)
            after = self.repo.active_branch_ref()
        output.done("Rebased {active_branch} onto {default_branch}/{default_remote}.")
        return before, after

    def rebase_and_push_active_branch(self):
        before, after = self.rebase_active_branch()
        output = Output(**self.context)
        with output.status(
            "Force pushing from {default_branch} "
            "to {default_branch}/{default_remote}..."
        ):
            self.repo.force_push(
                remote=self.repo.default_remote,
                local_branch=self.repo.active_branch,
                remote_branch=self.repo.active_branch,
                expect=before,
            )
        output.done(
            "Force pushed from {default_branch} "
            "to {default_branch}/{default_remote}."
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

    ctx.obj = Application(
        repo=Repo(
            config=Config(),
            gitpython=git.Repo(
                path=path,
                search_parent_directories=True,
            ),
        )
    )


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


@config.command(name="init")
@click.pass_obj
def config_init(app: Application):
    """
    Find and remember a default branch and default remote.

    This will be done automatically when you first use a command that works on a
    default branch or default remote.
    """
    app.init()


@config.command(name="default-branch")
@click.argument("branch", type=click.STRING)
@click.pass_obj
def config_set_default_branch(app: Application, branch: str) -> None:
    """
    Set the default branch.

    Sets 'switchbox.default-branch' in the repository's '.git/config' file.
    """
    app.set_default_branch(branch)


@config.command(name="default-remote")
@click.argument("remote", type=click.STRING)
@click.pass_obj
def config_set_default_remote(app: Application, remote: str) -> None:
    """
    Set the default remote.

    Sets 'switchbox.default-remote' in the repository's '.git/config' file.
    """
    app.set_default_remote(remote)


@main.command()
@dry_run_option
@remote_update_option
@click.pass_obj
def finish(app: Application, dry_run: bool, update_remotes: bool) -> None:
    """
    Finish working on a branch.

    Updates the default branch, switches to it, and deletes any merged branches.
    """
    if update_remotes:
        app.update_remotes()
    app.update_default_branch()
    app.switch_default_branch()
    app.remove_merged_branches(dry_run=dry_run)
    app.remove_rebased_branches(dry_run=dry_run)
    app.remove_squashed_branches(dry_run=dry_run)


@main.command()
@remote_update_option
@click.option(
    "--push/--no-push",
    "push",
    default=True,
    is_flag=True,
    help="Run 'git push --force-with-lease' after rebasing.",
)
@click.pass_obj
def rebase(app: Application, update_remotes: bool, push: bool) -> None:
    """
    Rebase the active branch, and force push it to the remote branch.

    The active branch is rebased on top of the remote default branch.

    The push is done with '--force-with-lease=<branch>:<before>' where <branch> is the
    active branch and <before> is the commit SHA of the active branch before the rebase.
    This ensures we only force push changes if our local state for the active branch
    exactly matches our remote state for the active branch.
    """
    if update_remotes:
        app.update_remotes()

    if push:
        app.rebase_and_push_active_branch()
    else:
        app.rebase_active_branch()


@main.command()
@click.pass_obj
def sparse(app: Application) -> None:
    """
    Configure sparse checkout for a repository.

    Excludes /.idea/ from being checked out.
    """
    output = Output()
    with output.status("Configuring sparse-checkout..."):
        app.repo.gitpython.git._call_process(
            "sparse-checkout",
            "set",
            "/*",
            "!/.idea/",
            insert_kwargs_after="set",
        )
    output.done("Configured sparse-checkout")
    with output.status("Reapplying sparse-checkout..."):
        app.repo.gitpython.git._call_process("sparse-checkout", "reapply")
    output.done("Reapplied sparse-checkout.")


@main.command()
@dry_run_option
@remote_update_option
@click.pass_obj
def tidy(app: Application, dry_run: bool, update_remotes: bool) -> None:
    """
    Cleans up branches.

    Removes remote branches that no longer exist and local branches that have been
    merged into the default branch.
    """
    if update_remotes:
        app.update_remotes()
    app.remove_merged_branches(dry_run=dry_run)
    app.remove_rebased_branches(dry_run=dry_run)
    app.remove_squashed_branches(dry_run=dry_run)


@main.command()
@click.pass_obj
def update(app: Application) -> None:
    """
    Update all git remotes.

    Just runs 'git remote update --prune'.
    """
    app.update_remotes()
