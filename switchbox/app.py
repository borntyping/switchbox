import collections
import dataclasses
import typing

import git
import inflect
import rich
import rich.console
import rich.progress
import rich.status
import rich.table
import rich.theme

from switchbox.repo import MaybeDeleteBranchPlan, Repo

T = typing.TypeVar("T")

OutputContextValue = typing.Union[typing.Callable[[], typing.Any], typing.Any]

console = rich.console.Console(
    theme=rich.theme.Theme(
        {
            "branch": "cyan",
            "remote": "blue",
        }
    )
)

p = inflect.engine()


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
    context: OutputContext

    def __init__(self, **context: OutputContextValue) -> None:
        self.context = OutputContext(context)

    def format(self, text: str) -> str:
        return text.format_map(self.context)

    def status(self, text: str) -> rich.status.Status:
        return rich.status.Status(self.format(text), speed=2.0)

    def done(self, task: str) -> None:
        console.print("[green]✓[/]", self.format(task), highlight=False)

    def enabled(self, task: str) -> None:
        console.print("[yellow]✓[/]", self.format(task), highlight=False)

    def disabled(self, task: str) -> None:
        console.print("[red]✓[/]", self.format(task), highlight=False)

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

    def remove_branches(self, dry_run: bool = True) -> None:
        target = self.repo.gitpython.heads[self.repo.default_branch]

        strategies: typing.Sequence[tuple[str, typing.Sequence[MaybeDeleteBranchPlan], list[git.Head], bool]] = [
            ("[green]merged[/]", self.repo.plan_delete_merged_branches(self.repo.gitpython, target), [], False),
            ("[yellow]rebased[/]", self.repo.plan_delete_rebased_branches(self.repo.gitpython, target), [], True),
            ("[magenta]squashed[/]", self.repo.plan_delete_squashed_branches(self.repo.gitpython, target), [], True),
        ]

        with rich.progress.Progress(
            rich.progress.SpinnerColumn(
                style="bar.complete",
                finished_text="[bar.finished]➔[/]",
            ),
            rich.progress.TextColumn(
                text_format="[progress.description]{task.description}",
                table_column=rich.table.Column(width=30),
            ),
            rich.progress.BarColumn(),
            rich.progress.TaskProgressColumn(),
            rich.progress.MofNCompleteColumn(table_column=rich.table.Column(width=9, justify="right")),
            rich.progress.TimeRemainingColumn(),
            rich.progress.TextColumn("{task.fields[plan]}"),
            rich.progress.TextColumn("{task.fields[step]}"),
        ) as progress:
            for merged, plans, delete, _ in strategies:
                total = sum(len(plan) for plan in plans)
                task = progress.add_task(f"Finding {merged} commits...", total=total, plan="", step="")

                # Completed holds the total of all completed *plans*, since we can skip
                # steps in a plan.
                completed = 0
                for plan in plans:
                    progress.update(task, completed=completed, plan=plan)
                    # If *any* step returns true, we can skip the remaining steps in the plan.
                    for i, step in enumerate(plan, start=1):
                        progress.update(task, completed=completed + i, step=step)
                        if step.delete():
                            delete.append(plan.head)
                            break
                    completed += len(plan)
                    progress.update(task, completed=completed, plan="", step="")

        for merged, _, delete, force in strategies:
            output = Output(
                merged=merged,
                one=len(delete),
                branch=p.plural("branch", len(delete)),
                was=p.plural_verb("was", len(delete)),
                target=Output.format_branch(self.repo.default_branch),
                items=Output.format_branches([head.name for head in delete]),
            )

            if not delete:
                output.done("There are no branches that have been {merged} into {target}.")
            elif dry_run:
                output.dry_run("Found {one} {branch} that {was} {merged} into {target} and can be removed: {items}.")
            else:
                with output.status("Removing {merged} {branch}..."):
                    self.repo.delete_branches(delete, force=force)
                output.done("Found and removed {one} {branch} " "that {was} {merged} into {target}: {items}.")

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
        with output.status("Force pushing from {default_branch} " "to {default_branch}/{default_remote}..."):
            self.repo.force_push(
                remote=self.repo.default_remote,
                local_branch=self.repo.active_branch,
                remote_branch=self.repo.active_branch,
                expect=before,
            )
        output.done("Force pushed from {default_branch} " "to {default_branch}/{default_remote}.")

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
