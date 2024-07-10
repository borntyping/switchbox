"""
Strategies for finding and removing branches that have been merged into a mainline branch.

This is a little messy, since finding squashed commits multiple units of work for each
branch we compare, which is why the algorithm is broken up so much.
"""

import dataclasses
import logging
import types
import typing

import git

from switchbox.ext.git import (
    contains_equivalent,
    is_squash_commit,
    merged,
    potential_squash_commits,
)

logger = logging.getLogger(__name__)


C = typing.TypeVar("C")


@dataclasses.dataclass()
class BranchesItem(typing.Generic[C]):
    head: git.Head
    commit: git.Commit
    context: C

    def __str__(self) -> str:
        return f"{self.commit.hexsha[:7]} {self.head.name}"


@dataclasses.dataclass(frozen=True)
class BranchesStrategy(typing.Generic[C]):
    force: typing.ClassVar[bool]

    gitpython: git.Repo
    upstream: git.Head

    def heads(self) -> typing.Sequence[git.Head]:
        """Return a list of git heads, excluding the upstream branch."""
        return [head for head in self.gitpython.heads if head != self.upstream]

    def generate(self) -> typing.Iterable[BranchesItem[C]]:
        """Generate multiple items for a head if needed. Avoids doing expensive work"""
        raise NotImplementedError

    def filter(self, item: BranchesItem[C]) -> bool:
        """Apply (usually expensive) filtering on a head+context."""
        return True

    def delete(self, items: typing.Iterable[BranchesItem[C]]) -> None:
        """Delete heads."""
        for item in items:
            if self.gitpython.active_branch == item.head:
                raise Exception("Refusing to remove the active branch")

            logger.info(
                "Deleting head %(branch)s",
                {"head": item.head.name, "force": self.force},
            )
            self.gitpython.delete_head(item.head, force=self.force)


@dataclasses.dataclass(frozen=True)
class MergedBranchesStrategy(BranchesStrategy[bool]):
    """
    Find branches merged into a target branch.

    This uses 'git branch --merged' to find merged branches.
    """

    force = False

    def generate(self) -> typing.Iterable[BranchesItem[bool]]:
        merged_heads = merged(self.gitpython, self.upstream)

        return [BranchesItem(head, head.commit, head in merged_heads) for head in self.heads()]

    def filter(self, item: BranchesItem) -> bool:
        return item.context


class RebasedBranchesStrategy(BranchesStrategy[None]):
    """
    Find branches rebased into a target branch.

    This uses 'git cherry <upstream> <branch>' to find branches where all
    commits from <branch> have an equivalent in the <default-branch> branch.

    https://git-scm.com/docs/git-cherry
    """

    force = True

    def generate(self) -> typing.Iterable[BranchesItem[None]]:
        return [BranchesItem(head, head.commit, None) for head in self.heads()]

    def filter(self, item: BranchesItem) -> bool:
        return contains_equivalent(self.gitpython, upstream=self.upstream, head=item.head)


class SquashedBranchesStrategy(BranchesStrategy[git.DiffIndex]):
    """
    Find branches squashed into a target branch.
    """

    force = True

    def generate(self) -> typing.Iterable[BranchesItem[git.DiffIndex]]:
        return [
            BranchesItem(head, commit, diff)
            for head in self.heads()
            for commit, diff in potential_squash_commits(self.gitpython, a=self.upstream, b=head)
        ]

    def filter(self, item: BranchesItem) -> bool:
        return is_squash_commit(self.gitpython, item.commit, item.context)
