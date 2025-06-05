import dataclasses
import logging
import typing

import git

from switchbox.ext.git import (
    contains_equivalent,
    commit_matches_diff,
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
        return bool(self.is_rebased or self.is_squashed)

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
        logging.info("Checking if '%(branch)s' was merged", {"branch": self.branch})
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
        logging.info("Checking if '%(branch)s' was rebased", {"branch": self.branch})
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
            logging.debug(
                "Branch has already been marked as squashed (branch='%(branch)s', commit='%(commit)s')",
                {"branch": self.branch, "commit": self.commit},
            )
            return

        if self.skip:
            logging.debug(
                "Branch has already been marked as skipped (branch='%(branch)s', commit='%(commit)s')",
                {"branch": self.branch, "commit": self.commit},
            )
            return

        self.branch.is_squashed = commit_matches_diff(self.commit, self.diff)
        self.branch.last_compared_upstream = self.branch.upstream
        self.branch.last_compared_commit = self.commit
        logging.info(
            "Checked if branch was squashed (branch='%(branch)s', commit='%(commit)s', is_squashed=%(is_squashed)s)",
            {"branch": self.branch, "commit": self.commit, "is_squashed": self.branch.is_squashed},
        )

    @classmethod
    def from_branches(cls, branches: list[Branch]) -> list[typing.Self]:
        steps = []
        for branch in branches:
            diff, commits = potential_squash_commits(repo=branch.repo, a=branch.upstream, b=branch.head)

            # We can skip any commits before last_compared_commit as long as
            # we're checking the same upstream.
            if branch.upstream != branch.last_compared_upstream:
                logging.debug(
                    "Upstream doesn't match last compared upstream, resetting "
                    "(branch='%(branch)s', upstream='%(upstream)s', last_compared_upstream='%(last_compared_upstream)s')",
                    {
                        "branch": branch,
                        "upstream": branch.upstream,
                        "last_compared_upstream": branch.last_compared_upstream,
                    },
                )
                branch.last_compared_upstream = None
                branch.last_compared_commit = None
                pivot = 0
            elif branch.last_compared_commit not in commits:
                logging.debug(
                    "Last compared commit is not longer in the branch, resetting "
                    "(branch='%(branch)s', last_compared_commit='%(last_compared_commit)s')",
                    {
                        "branch": branch,
                        "last_compared_commit": branch.last_compared_commit,
                    },
                )
                branch.last_compared_upstream = None
                branch.last_compared_commit = None
                pivot = 0
            else:
                pivot = commits.index(branch.last_compared_commit)

            for index, commit in enumerate(commits):
                steps.append(cls(branch=branch, commit=commit, diff=diff, skip=index < pivot))
        return steps


@dataclasses.dataclass
class BranchManager:
    branches: list[Branch]

    @classmethod
    def from_repo(
        cls,
        repo: git.Repo,
        local_default_branch: str,
        remote_default_branch: str,
    ) -> typing.Self:
        upstream = repo.references[remote_default_branch]
        ignore = list_in_use_heads(repo) | {repo.heads[local_default_branch]}
        heads = [head for head in repo.heads if head not in ignore]
        branches = [Branch.from_repo(repo, head, upstream) for head in heads]
        return cls(branches=branches)

    def filter_branches(self, names: typing.Collection[str]) -> typing.Self:
        if not names:
            return self
        return dataclasses.replace(self, branches=[b for b in self.branches if b.head.name in names])

    @property
    def set_branch_is_merged_steps(self) -> list[SetBranchIsMergedStep]:
        return SetBranchIsMergedStep.from_branches(self.branches)

    @property
    def set_branch_is_rebased_steps(self) -> list[SetBranchIsRebasedStep]:
        return SetBranchIsRebasedStep.from_branches(self.branches)

    @property
    def set_branch_is_squashed_steps(self) -> list[SetBranchIsSquashedStep]:
        return SetBranchIsSquashedStep.from_branches(self.branches)

    @property
    def branches_to_remove(self) -> list[Branch]:
        return [branch for branch in self.branches if branch.can_be_removed]

    def load_config(self) -> None:
        for branch in self.branches:
            branch.load_config()

    def save_config(self) -> None:
        for branch in self.branches:
            branch.save_config()
