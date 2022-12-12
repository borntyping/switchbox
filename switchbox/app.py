import collections
import dataclasses
import typing

import click
import inflect
import rich
import rich.console
import rich.status
import rich.theme

from switchbox.repo import Repo

console = rich.console.Console(
    theme=rich.theme.Theme(
        {
            "branch": "cyan",
            "remote": "blue",
        }
    )
)

OutputContextValue = typing.Union[typing.Callable[[], typing.Any], typing.Any]


def plural(text: str, items: typing.Sized) -> str:
    return f"{len(items)} {p.plural(text, len(items))}"


def join(items: typing.Collection) -> str:
    return p.join(list(sorted(str(item) for item in items)))


p = inflect.engine()


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

        with output.status(
            "Updating branch {default_branch} "
            "to match {default_remote}/{default_branch}."
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

        if self.repo.active_branch == self.repo.default_branch:
            output.done("Already on the {default_branch} branch.")
            return

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
        with output.status("Rebasing onto {default_remote}/{default_branch}..."):
            before = self.repo.active_branch_ref()
            self.repo.rebase(upstream=self.repo.remote_default_branch)
            after = self.repo.active_branch_ref()
        output.done("Rebased {active_branch} onto {default_remote}/{default_branch}.")
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
