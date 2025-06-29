import logging
import os
import typing

import click.globals
import git
import rich.logging

from switchbox.app import Application, CONSOLE
from switchbox.repo import Config, Repo

remote_update_option = click.option(
    "--update/--no-update",
    "update_remotes",
    default=True,
    is_flag=True,
    help="Run 'git remote update --prune' first.",
)
dry_run_option = click.option(
    "--dry-run/--no-dry-run",
    default=False,
    is_flag=True,
)

option_merged = click.option(
    "--merged/--no-merged",
    "enable_merged",
    default=True,
    is_flag=True,
    help="Include merged branches.",
)
option_rebased = click.option(
    "--rebased/--no-rebased",
    "enable_rebased",
    default=True,
    is_flag=True,
    help="Include rebased branches.",
)
option_squashed = click.option(
    "--squashed/--no-squashed",
    "enable_squashed",
    default=True,
    is_flag=True,
    help="Include squashed branches.",
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
    logging.basicConfig(
        level=verbosity[verbose],
        format="%(message)s",
        handlers=[rich.logging.RichHandler(console=CONSOLE, show_time=False)],
    )

    ctx.obj = Application(
        repo=Repo(
            config=Config(),
            gitpython=git.Repo(
                path=path,
                search_parent_directories=True,
            ),
        )
    )


@main.group()
def config() -> None:
    """
    Manage options used by switchbox stored in '.git/config'.
    """
    pass


@config.command(name="init")
@click.pass_obj
def config_init(app: Application):
    """
    Find and remember a default branch and default remote.

    This will be done automatically when you first use a command that works on a
    default branch or default remote.
    """
    app.init()


@config.command(name="show")
@click.pass_obj
def config_show(app: Application):
    """
    Display the git config options used by switchbox.
    """
    print(app.repo.get_config())


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


@config.command(name="clean")
@click.pass_obj
def config_clean(app: Application) -> None:
    app.clean_config()


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
    app.remove_branches(dry_run=dry_run)


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
@option_merged
@option_rebased
@option_squashed
@click.argument("branch", type=click.STRING, nargs=-1)
@click.pass_obj
def tidy(
    app: Application,
    dry_run: bool,
    enable_merged: bool,
    enable_rebased: bool,
    enable_squashed: bool,
    update_remotes: bool,
    branch: list[str],
) -> None:
    """
    Cleans up branches.

    Removes remote branches that no longer exist and local branches that have been
    merged into the default branch.
    """
    if update_remotes:
        app.update_remotes()

    app.remove_branches(
        dry_run=dry_run,
        enable_merged=enable_merged,
        enable_rebased=enable_rebased,
        enable_squashed=enable_squashed,
        names=branch,
    )


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
