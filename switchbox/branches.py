import dataclasses
import logging
import typing

import git

from switchbox.ext.git import (
    contains_equivalent,
    is_squash_commit,
    list_in_use_heads,
    list_merged_heads,
    potential_squash_commits,
)
from switchbox.repo import SECTION

LAST_COMPARED_UPSTREAM = "lastComparedUpstream"
LAST_COMPARED_COMMIT = "lastComparedCommit"

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class Branch:
    repo: git.Repo
    head: git.Head
    upstream: git.Reference

    is_merged: bool | None = None
    is_rebased: bool | None = None
    is_squashed: bool | None = None

    last_compared_upstream: git.Reference | None = None
    last_compared_commit: git.Commit | None = None

    @classmethod
    def from_repo(cls, repo: git.Repo, head: git.Head, upstream: git.Reference) -> typing.Self:
        branch = cls(repo=repo, head=head, upstream=upstream)
        branch.load_config()
        return branch

    def __post_init__(self) -> None:
        if self.head == self.repo.active_branch:
            raise Exception("Can't manage the active branch!")
        if not self.head.is_valid():
            raise Exception(f"Head {self.head} is not valid!")

    def __str__(self) -> str:
        return self.head.name

    @property
    def can_be_removed(self) -> bool:
        return bool(self.is_merged or self.is_rebased or self.is_squashed)

    @property
    def use_force_when_removing(self) -> bool:
        return bool(self.is_rebased or self.is_rebased)

    @property
    def subsection(self) -> str:
        return f'{SECTION} "{self.head.name}"'

    def delete(self) -> None:
        self.repo.delete_head(self.head, force=self.use_force_when_removing)
        with self.repo.config_writer() as writer:
            if writer.has_section(self.subsection):
                writer.remove_section(self.subsection)

    def load_config(self) -> None:
        with self.repo.config_reader() as reader:
            if reader.has_section(self.subsection):
                if reader.has_option(self.subsection, LAST_COMPARED_UPSTREAM):
                    upstream = reader.get_value(self.subsection, LAST_COMPARED_UPSTREAM)
                    assert isinstance(upstream, str)
                    self.last_compared_upstream = self.repo.references[upstream]
                if reader.has_option(self.subsection, LAST_COMPARED_COMMIT):
                    commit = reader.get_value(self.subsection, LAST_COMPARED_COMMIT)
                    assert isinstance(commit, str)
                    self.last_compared_commit = self.repo.commit(commit)

    def save_config(self) -> None:
        with self.repo.config_writer() as writer:
            if not writer.has_section(self.subsection):
                writer.add_section(self.subsection)

            if self.last_compared_upstream is not None and self.last_compared_commit is not None:
                writer.set_value(self.subsection, LAST_COMPARED_UPSTREAM, self.last_compared_upstream.name)
                writer.set_value(self.subsection, LAST_COMPARED_COMMIT, self.last_compared_commit.hexsha)
            else:
                writer.remove_option(self.subsection, LAST_COMPARED_UPSTREAM)
                writer.remove_option(self.subsection, LAST_COMPARED_COMMIT)


@dataclasses.dataclass()
class SetBranchIsMergedStep:
    branch: Branch

    def __str__(self) -> str:
        return str(self.branch)

    def __call__(self) -> None:
        self.branch.is_merged = self.branch.head in list_merged_heads(
            repo=self.branch.repo,
            into=self.branch.upstream,
        )

    @classmethod
    def from_branches(cls, branches: list[Branch]) -> list[typing.Self]:
        return [cls(branch) for branch in branches]


@dataclasses.dataclass()
class SetBranchIsRebasedStep:
    branch: Branch

    def __str__(self) -> str:
        return str(self.branch)

    def __call__(self) -> None:
        self.branch.is_rebased = contains_equivalent(
            repo=self.branch.repo,
            upstream=self.branch.upstream,
            head=self.branch.head,
        )

    @classmethod
    def from_branches(cls, branches: list[Branch]) -> list[typing.Self]:
        return [cls(branch) for branch in branches]


@dataclasses.dataclass()
class SetBranchIsSquashedStep:
    branch: Branch
    commit: git.Commit
    diff: git.DiffIndex
    skip: bool = False

    def __str__(self) -> str:
        return f"{self.branch} {self.commit}"

    def __call__(self) -> None:
        if self.branch.is_squashed:
            return

        if self.skip:
            return

        self.branch.is_squashed = is_squash_commit(self.branch.repo, self.commit, self.diff)
        self.branch.last_compared_upstream = self.branch.upstream
        self.branch.last_compared_commit = self.commit

    @classmethod
    def from_branches(cls, branches: list[Branch]) -> list[typing.Self]:
        steps = []
        for branch in branches:
            diff, commits = potential_squash_commits(repo=branch.repo, a=branch.upstream, b=branch.head)

            # We can skip any commits before lastComparedCommit (as long as
            # we're checking the same upstream).
            if branch.upstream == branch.last_compared_upstream:
                pivot = commits.index(branch.last_compared_commit)
            else:
                pivot = 0

            for index, commit in enumerate(commits):
                steps.append(cls(branch=branch, commit=commit, diff=diff, skip=index < pivot))
        return steps


@dataclasses.dataclass
class BranchManager:
    branches: list[Branch]
    set_branch_is_merged_steps: list[SetBranchIsMergedStep]
    set_branch_is_rebased_steps: list[SetBranchIsRebasedStep]
    set_branch_is_squashed_steps: list[SetBranchIsSquashedStep]

    @classmethod
    def from_repo(cls, repo: git.Repo, local_default_branch: str, remote_default_branch: str) -> typing.Self:
        upstream = repo.references[remote_default_branch]
        ignore = list_in_use_heads(repo) | {repo.heads[local_default_branch]}
        heads = [head for head in repo.heads if head not in ignore]
        branches = [Branch.from_repo(repo, head, upstream) for head in heads]
        return cls(
            branches=branches,
            set_branch_is_merged_steps=SetBranchIsMergedStep.from_branches(branches),
            set_branch_is_rebased_steps=SetBranchIsRebasedStep.from_branches(branches),
            set_branch_is_squashed_steps=SetBranchIsSquashedStep.from_branches(branches),
        )

    @property
    def branches_to_remove(self) -> list[Branch]:
        return [branch for branch in self.branches if branch.can_be_removed]

    def load_config(self) -> None:
        for branch in self.branches:
            branch.load_config()

    def save_config(self) -> None:
        for branch in self.branches:
            branch.save_config()
