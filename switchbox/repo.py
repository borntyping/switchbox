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
    list_in_use_heads,
    list_merged_heads,
    potential_squash_commits,
)

logger = logging.getLogger(__name__)

F = typing.TypeVar("F", bound=typing.Callable)
Named = typing.TypeVar("Named", git.Head, git.Remote)

SECTION = "switchbox"


@dataclasses.dataclass()
class MaybeDeleteBranchStep:
    index: int
    commit: git.Commit
    merged: bool

    def __str__(self) -> str:
        return self.commit.hexsha[:7]


class MaybeDeleteBranchPlan(typing.Sized, typing.Iterable[MaybeDeleteBranchStep]):
    head: git.Head
    merged: bool

    def __str__(self) -> str:
        return self.head.name

    def __len__(self) -> int:
        raise NotImplementedError

    def __iter__(self) -> typing.Iterator[MaybeDeleteBranchStep]:
        raise NotImplementedError


@dataclasses.dataclass()
class MaybeDeleteMergedBranchPlan(MaybeDeleteBranchPlan):
    head: git.Head
    merged_heads: set[git.Head]

    merged: bool = dataclasses.field(default=False, init=False)

    def __len__(self) -> int:
        return 1

    def __iter__(self) -> typing.Iterator[MaybeDeleteBranchStep]:
        self.merged = self.head in self.merged_heads
        yield MaybeDeleteBranchStep(index=1, commit=self.head.commit, merged=self.merged)


@dataclasses.dataclass()
class MaybeDeleteRebasedBranchPlan(MaybeDeleteBranchPlan):
    repo: git.Repo
    head: git.Head = dataclasses.field()
    upstream: git.Reference = dataclasses.field(kw_only=True)

    merged: bool = dataclasses.field(default=False, init=False)

    def __len__(self) -> int:
        return 1

    def __iter__(self) -> typing.Iterator[MaybeDeleteBranchStep]:
        self.merged = contains_equivalent(repo=self.repo, upstream=self.upstream, head=self.head)
        yield MaybeDeleteBranchStep(index=1, commit=self.head.commit, merged=self.merged)


@dataclasses.dataclass()
class MaybeDeleteSquashedBranchPlan(MaybeDeleteBranchPlan):
    """
    A list of steps that check if a branch has been merged and can be deleted.
    If *any* step returns True, this branch can be deleted.
    """

    repo: git.Repo
    head: git.Head

    diff: git.DiffIndex
    commits: typing.Sequence[git.Commit]

    checked: git.Commit | None = dataclasses.field(default=None)
    merged: bool = dataclasses.field(default=False, init=False)

    def __len__(self) -> int:
        return len(self.commits)

    def __iter__(self) -> typing.Iterator[MaybeDeleteBranchStep]:
        """
        The split should be the point in the list of commits where all the earlier
        commits have already been checked in a previous invocation of the CLI.

        The commit referenced by 'self.checked' might not be in 'self.commits' if the
        branch has been rebased since switchbox last ran.
        """
        if not self.commits:
            return

        if self.checked is None:
            split = 0
        elif self.checked not in self.commits:
            split = 0
        else:
            split = self.commits.index(self.checked)

        for index, commit in enumerate(self.commits):
            merged = is_squash_commit(self.repo, commit, self.diff) if index >= split else False
            self.checked = commit
            self.merged = self.merged or merged
            yield MaybeDeleteBranchStep(index=index, commit=commit, merged=merged)


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
            logger.info("Deleting head %(head)s", {"head": head.name, "force": force})

            if self.gitpython.active_branch == head:
                raise Exception("Refusing to remove the active branch")

            if not head.is_valid():
                logger.warning("Head %(head)s is not valid", {"head": head.name})
                return

            self.gitpython.delete_head(head, force=force)

    def _heads(self):
        """Exclude the default branch and worktrees."""
        exclude = list_in_use_heads(self.gitpython) | {self.gitpython.heads[self.default_branch]}
        return (head for head in self.gitpython.heads if head not in exclude)

    def plan_delete_merged_branches(self, upstream: git.Reference) -> list[MaybeDeleteMergedBranchPlan]:
        merged = list_merged_heads(self.gitpython, upstream)
        return [MaybeDeleteMergedBranchPlan(head, merged) for head in self._heads()]

    def plan_delete_rebased_branches(self, upstream: git.Reference) -> list[MaybeDeleteBranchPlan]:
        return [MaybeDeleteRebasedBranchPlan(self.gitpython, h, upstream=upstream) for h in self._heads()]

    def plan_delete_squashed_branches(self, upstream: git.Reference) -> list[MaybeDeleteSquashedBranchPlan]:
        with self.gitpython.config_reader("repository") as reader:
            return [self._plan_delete_squashed_branches(upstream, head, reader) for head in self._heads()]

    def _plan_delete_squashed_branches(
        self,
        upstream: git.Reference,
        head: git.Head,
        reader: git.GitConfigParser,
    ) -> MaybeDeleteSquashedBranchPlan:
        section = f'{SECTION} "{head.name}"'

        checked = None
        if reader.has_section(section):
            if upstream.name != reader.get(section, "upstream", fallback=upstream.name):
                raise Exception("Upstream changed")

            if value := reader.get(section, "squashed", fallback=None):
                checked = self.gitpython.commit(value)

        diff, commits = potential_squash_commits(self.gitpython, a=upstream, b=head)

        return MaybeDeleteSquashedBranchPlan(
            repo=self.gitpython,
            head=head,
            diff=diff,
            commits=commits,
            checked=checked,
        )

    def done_delete_squashed_branches(
        self,
        upstream: git.Reference,
        plans: typing.Iterable[MaybeDeleteSquashedBranchPlan],
    ) -> None:
        # TODO: Store a hash of the diffindex; since if the branch changes our comparison is now invalid
        with self.gitpython.config_writer("repository") as writer:
            for plan in plans:
                section = f'{SECTION} "{plan.head.name}"'
                if not writer.has_section(section):
                    writer.add_section(section)
                if plan.checked is not None:
                    writer.set(section, "upstream", upstream.name)
                    writer.set(section, "squashed", plan.checked.hexsha)
