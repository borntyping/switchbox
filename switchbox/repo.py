"""
Strategies for finding and removing branches that have been merged into a mainline branch.

This is a little messy, since finding squashed commits multiple units of work for each
branch we compare, which is why the algorithm is broken up so much.
"""

import dataclasses
import logging
import pathlib
import typing

import click
import git

from switchbox.ext.git import (
    contains_equivalent,
    is_squash_commit,
    list_merged_heads,
    potential_squash_commits,
)

logger = logging.getLogger(__name__)

F = typing.TypeVar("F", bound=typing.Callable)
Named = typing.TypeVar("Named", git.Head, git.Remote)

SECTION = "switchbox"


@dataclasses.dataclass()
class MaybeDeleteStep:
    """
    A unit of work that checks if a branch has been merged and can be deleted.
    """

    commit: git.Commit
    delete: typing.Callable[[], bool]

    def __str__(self):
        return self.commit.hexsha[:7]


@dataclasses.dataclass()
class MaybeDeleteBranchPlan(typing.Sized, typing.Iterable[MaybeDeleteStep]):
    """
    A list of steps that check if a branch has been merged and can be deleted.
    If *any* step returns True, this branch can be deleted.
    """

    head: git.Head
    steps: list[MaybeDeleteStep]

    def __str__(self) -> str:
        return self.head.name

    def __len__(self) -> int:
        return len(self.steps)

    def __iter__(self) -> typing.Iterator[MaybeDeleteStep]:
        return iter(self.steps)


@dataclasses.dataclass(frozen=True)
class GitOption:
    section: str
    option: str
    value: str

    def __str__(self) -> str:
        return f"{self.section}.{self.option}={self.value}"


@dataclasses.dataclass(frozen=True)
class Config:
    default_branch_names: typing.Sequence[str] = ("main", "master")
    default_remote_names: typing.Sequence[str] = ("upstream", "origin")
    write_config: bool = True
    application: str = "switchbox"
    sparse_checkout_exclude: typing.Sequence[str] = ("/.idea/",)


class RepositoryException(click.ClickException):
    pass


@dataclasses.dataclass(frozen=True)
class Repo:
    gitpython: git.Repo
    config: Config

    @property
    def path(self) -> pathlib.Path:
        if working_tree_dir := self.gitpython.working_tree_dir:
            return pathlib.Path(working_tree_dir)

        return pathlib.Path(self.gitpython.git_dir)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def pretty_path(self) -> str:
        home = pathlib.Path.home()
        try:
            return f"~/{self.path.relative_to(home)}"
        except ValueError:
            return f"{self.path}"

    @property
    def active_branch(self) -> str:
        return self.gitpython.active_branch.name

    @property
    def default_branch(self) -> str:
        return self.get("default-branch") or self.detect_default_branch()

    @property
    def default_remote(self) -> str:
        return self.get("default-remote") or self.detect_default_remote()

    @property
    def remote_default_branch(self) -> str:
        return f"{self.default_remote}/{self.default_branch}"

    @property
    def section(self) -> str:
        return self.config.application

    def detect_default_branch(self) -> str:
        if not self.gitpython.heads:
            raise RepositoryException("Repository has no branches. Is this a new repository?")

        if head := self._first_match(
            items=self.gitpython.heads,
            names=self.config.default_branch_names,
        ):
            self.set("default-branch", head.name)
            return head.name

        raise RepositoryException(f"Could not find a default branch for {self}.")

    def detect_default_remote(self) -> str:
        if not self.gitpython.heads:
            raise RepositoryException("Repository has no remotes. Is this a new repository?")

        if remote := self._first_match(
            items=self.gitpython.remotes,
            names=self.config.default_remote_names,
        ):
            self.set("default-remote", remote.name)
            return remote.name

        raise RepositoryException(f"Could not find a default remote for {self}.")

    def get(self, option: str, *, section: str = SECTION) -> typing.Optional[str]:
        with self.gitpython.config_reader() as reader:
            if reader.has_section(section):
                if reader.has_option(section, option):
                    value = reader.get_value(section, option)

                    if isinstance(value, (int, float)):
                        raise RepositoryException("Unexpected value for {}.{}: {!r}".format(section, option, value))

                    return value
        return None

    def set(self, option: str, value: str, section: str = SECTION) -> GitOption:
        with self.gitpython.config_writer("repository") as writer:
            if not writer.has_section(section):
                writer.add_section(section)
            writer.set(section, option, value)

        return GitOption(section, option, value)

    def remove_option(self, option: str, section: str = SECTION) -> bool:
        with self.gitpython.config_writer("repository") as writer:
            if writer.has_section(section) and writer.has_option(section, option):
                writer.remove_option(section, option)
                return True
        return False

    def get_config(self) -> str:
        lines = []

        with self.gitpython.config_reader() as reader:
            if not reader.has_section(SECTION):
                return ""

            lines.append(f"[{SECTION}]")
            for option, value in reader.items(SECTION):
                lines.append(f"\t{option} = {value}")

        return "\n".join(lines)

    @staticmethod
    def _first_match(
        items: typing.List[Named],
        names: typing.Sequence[str],
    ) -> typing.Optional[Named]:
        matches = [item for item in items if item.name in names]
        if matches:
            return matches[0]
        return None

    def update_remotes(self) -> None:
        self.gitpython.git._call_process(
            "remote",
            "update",
            insert_kwargs_after="update",
            prune=True,
        )

    def update_branch_from_remote(self, remote: str, branch: str) -> None:
        if self.gitpython.active_branch.name == branch:
            self.gitpython.remotes[remote].pull()
        else:
            self.gitpython.git.branch(branch, f"{remote}/{branch}", force=True)

    def switch(self, branch: str) -> None:
        if self.gitpython.active_branch.name == branch:
            logger.info(
                "Not switching to branch %(branch), already on it",
                {"branch", branch},
            )
            return

        self.gitpython.git.switch(branch)

    def rebase(self, upstream: str) -> None:
        self.gitpython.git.rebase(upstream, update_refs=True)

    def force_push(self, remote: str, local_branch: str, remote_branch: str, expect: str) -> None:
        self.gitpython.git.push(
            remote,
            f"{local_branch}:{remote_branch}",
            force_with_lease=f"{remote_branch}:{expect}",
        )

    def active_branch_ref(self) -> str:
        return self.gitpython.active_branch.commit.hexsha

    def sparse_checkout_set(self) -> tuple[list[str], list[str]]:
        include = ["/*"]
        exclude = [f"!{path}" for path in self.config.sparse_checkout_exclude]
        logger.info(
            "Setting sparse-checkout paths " "to include %(include)r and exclude %(exclude)r",
            {"include": include, "exclude": exclude},
        )
        self.gitpython.git._call_process(
            "sparse-checkout",
            "set",
            *include,
            *exclude,
            insert_kwargs_after="set",
            no_cone=True,
        )
        return include, exclude

    def sparse_checkout_reapply(self) -> None:
        self.gitpython.git._call_process("sparse-checkout", "reapply")

    def delete_branches(self, heads: typing.Sequence[git.Head], force: bool = False) -> None:
        for head in heads:
            if self.gitpython.active_branch == head:
                raise Exception("Refusing to remove the active branch")

            logger.info(
                "Deleting head %(branch)s",
                {"head": head.name, "force": force},
            )
            self.gitpython.delete_head(head, force=force)

    def plan_delete_merged_branches(self, gitpython: git.Repo, target: git.Head) -> list[MaybeDeleteBranchPlan]:
        merged = list_merged_heads(gitpython, target)
        return [
            MaybeDeleteBranchPlan(
                head=head,
                steps=[
                    MaybeDeleteStep(head.commit, lambda: head in merged),
                ],
            )
            for head in gitpython.heads
            if head != target
        ]

    def plan_delete_rebased_branches(self, gitpython: git.Repo, target: git.Head) -> list[MaybeDeleteBranchPlan]:
        return [
            MaybeDeleteBranchPlan(
                head=head,
                steps=[
                    MaybeDeleteStep(head.commit, lambda: contains_equivalent(gitpython, target, head)),
                ],
            )
            for head in gitpython.heads
            if head != target
        ]

    def plan_delete_squashed_branches(self, gitpython: git.Repo, target: git.Head) -> list[MaybeDeleteBranchPlan]:
        return [
            MaybeDeleteBranchPlan(
                head=head,
                steps=[
                    MaybeDeleteStep(commit, lambda: is_squash_commit(gitpython, commit, diff))
                    for (commit, diff) in potential_squash_commits(gitpython, a=target, b=head)
                ],
            )
            for head in gitpython.heads
            if head != target
        ]
