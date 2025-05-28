import collections
import dataclasses
import logging
import typing

import inflect
import rich
import rich.console
import rich.progress
import rich.status
import rich.table
import rich.theme

from switchbox.branches import BranchManager
from switchbox.repo import Repo

OutputContextValue = typing.Union[typing.Callable[[], typing.Any], typing.Any]

CONSOLE = rich.console.Console(
    theme=rich.theme.Theme(
        {
            "branch": "cyan",
            "remote": "blue",
        }
    )
)

p = inflect.engine()

logger = logging.getLogger(__name__)


def plural(text: str, items: typing.Sized) -> str:
    return f"{len(items)} {p.plural(text, len(items))}"


def join(items: typing.Collection) -> str:
    return p.join(list(sorted(str(item) for item in items)))


class OutputContext(collections.UserDict[str, OutputContextValue]):
    def __getitem__(self, item: str) -> str:
        value = super().__getitem__(item)
        return value() if callable(value) else value


@dataclasses.dataclass()
class Output:
    console: rich.console.Console
    context: OutputContext

    def __init__(self, console: rich.console.Console = CONSOLE, **context: OutputContextValue) -> None:
        self.console = console
        self.context = OutputContext(context)

    def format(self, text: str) -> str:
        return text.format_map(self.context)

    def status(self, text: str) -> rich.status.Status:
        return rich.status.Status(self.format(text), speed=2.0)

    def done(self, task: str) -> None:
        self.console.print("[bar.finished]✓[/]", self.format(task), highlight=False)

    def enabled(self, task: str) -> None:
        self.console.print("[yellow]✓[/]", self.format(task), highlight=False)

    def disabled(self, task: str) -> None:
        self.console.print("[red]✓[/]", self.format(task), highlight=False)

    def dry_run(self, task: str) -> None:
        self.console.print("[yellow]➔[/]", self.format(task), highlight=False)

    @classmethod
    def format_branch(cls, branch: str | None) -> str:
        return f"[branch]{branch}[/]" if branch else "[red]UNSET[/]"

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

        with output.status("Updating branch {default_branch} " "to match {default_remote}/{default_branch}."):
            self.repo.update_branch_from_remote(
                remote=self.repo.default_remote,
                branch=self.repo.default_branch,
            )
        output.done("Updated branch {default_branch} " "to match {default_branch}/{default_remote}.")

    def switch_default_branch(self) -> None:
        output = Output(**self.context)

        if self.repo.active_branch == self.repo.default_branch:
            output.done("Already on the {default_branch} branch.")
            return

        with output.status("Switching to the {default_branch} branch..."):
            self.repo.switch(self.repo.default_branch)
        output.done("Switched to the {default_branch} branch.")

    def remove_branches(
        self,
        enable_merged: bool = True,
        enable_rebased: bool = True,
        enable_squashed: bool = True,
        dry_run: bool = True,
    ) -> None:
        with rich.progress.Progress(
            rich.progress.SpinnerColumn(
                style="bar.complete",
                finished_text="[bar.finished]✓[/]",
            ),
            rich.progress.TextColumn(
                text_format="[progress.description]{task.description}",
                table_column=rich.table.Column(width=30),
            ),
            rich.progress.BarColumn(),
            rich.progress.TaskProgressColumn(),
            rich.progress.MofNCompleteColumn(table_column=rich.table.Column(width=9, justify="center")),
            rich.progress.TimeElapsedColumn(),
            console=CONSOLE,
        ) as progress:
            task_id = progress.add_task("Finding inactive branches...", total=False)
            branch_manager = BranchManager.from_repo(
                repo=self.repo.gitpython,
                local_default_branch=self.repo.default_branch,
                remote_default_branch=self.repo.remote_default_branch,
            )
            branch_manager.load_config()
            progress.update(
                task_id=task_id,
                total=len(branch_manager.branches),
                completed=len(branch_manager.branches),
            )

            if enable_merged:
                for set_branch_is_merged in progress.track(
                    sequence=branch_manager.set_branch_is_merged_steps,
                    description="Finding [green]merged[/] commits...",
                ):
                    set_branch_is_merged()

            if enable_rebased:
                for set_branch_is_rebased in progress.track(
                    sequence=branch_manager.set_branch_is_rebased_steps,
                    description="Finding [yellow]rebased[/] commits...",
                ):
                    set_branch_is_rebased()

            if enable_squashed:
                for set_branch_is_squashed in progress.track(
                    sequence=branch_manager.set_branch_is_squashed_steps,
                    description="Finding [magenta]squashed[/] commits...",
                ):
                    set_branch_is_squashed()

            for branch in progress.track(
                sequence=branch_manager.branches_to_remove,
                total=len(branch_manager.branches_to_remove),
                description="Tidying branches...",
            ):
                merged = []
                if branch.is_merged:
                    merged.append("[green]merged[/]")
                if branch.is_rebased:
                    merged.append("[yellow]rebased[/]")
                if branch.is_squashed:
                    merged.append("[magenta]squashed[/]")

                output = Output(branch=branch.head.name, merged=merged, upstream=branch.upstream.name)
                if dry_run:
                    output.dry_run("Branch {branch} was {merged} into {upstream} and can be removed.")
                else:
                    branch.delete()
                    output.done("Branch {branch} was {merged} into {upstream} and was removed.")

        branch_manager.save_config()

    def rebase_active_branch(self) -> typing.Tuple[str, str]:
        """Rebase the active branch on top of the remote default branch."""
        output = Output(**self.context)
        with output.status("Rebasing onto {default_remote}/{default_branch}..."):
            before = self.repo.active_branch_ref()
            self.repo.rebase(upstream=self.repo.remote_default_branch)
            after = self.repo.active_branch_ref()
        output.done("Rebased {active_branch} onto {default_remote}/{default_branch}.")
        return before, after

    def rebase_and_push_active_branch(self):
        before, after = self.rebase_active_branch()
        output = Output(**self.context)
        with output.status("Force pushing from {active_branch} to {default_remote}/{active_branch}..."):
            self.repo.force_push(
                remote=self.repo.default_remote,
                local_branch=self.repo.active_branch,
                remote_branch=self.repo.active_branch,
                expect=before,
            )
        output.done("Force pushed from {active_branch} to {default_remote}/{active_branch}.")

    def configure_sparse_checkout(self) -> None:
        output = Output()
        with output.status("Configuring sparse-checkout..."):
            include, exclude = self.repo.sparse_checkout_set()
        output.done("Configured sparse-checkout.")
        output.enabled("Including {}.".format(join(include)))
        output.disabled("Excluding {}.".format(join(exclude)))
        with output.status("Reapplying sparse-checkout..."):
            self.repo.sparse_checkout_reapply()
        output.done("Reapplied sparse-checkout.")
