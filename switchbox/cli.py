import logging
import os
import typing

import click.globals
import git

from switchbox.app import Application
from switchbox.repo import Config, Repo

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
    default=False,
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
    app.configure_sparse_checkout()


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
@remote_update_option
@click.pass_obj
def update(app: Application, update_remotes: bool) -> None:
    """
    Update the default branch.

    Remotes are updated with 'git remote update --prune'.

    If the repository is currently on the default branch it will be pulled. If
    the repository is on any other branch, it will be edited to point at the
    same commit as the upstream default branch.
    """
    if update_remotes:
        app.update_remotes()
    app.update_default_branch()
