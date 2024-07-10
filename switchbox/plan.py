"""
Strategies for finding and removing branches that have been merged into a mainline branch.

This is a little messy, since finding squashed commits multiple units of work for each
branch we compare, which is why the algorithm is broken up so much.
"""

import dataclasses
import logging
import typing

import git

from switchbox.ext.git import (
    contains_equivalent,
    is_squash_commit,
    merged,
    potential_squash_commits,
)

logger = logging.getLogger(__name__)


@dataclasses.dataclass()
class MaybeDeleteStep:
    branch: git.Head
    commit: git.Commit
    delete: typing.Callable[[], bool]

    def __str__(self):
        return f"{self.commit.hexsha[:7]} {self.branch.name}"


@dataclasses.dataclass()
class MaybeDeletePlan:
    """If *any* step returns True, this branch can be deleted."""

    branch: git.Head
    steps: list[MaybeDeleteStep]

    def count(self) -> int:
        return len(self.steps)

    @classmethod
    def merged(cls, gitpython: git.Repo, target: git.Head) -> list["MaybeDeletePlan"]:
        branches = [head for head in gitpython.heads if head != target]
        merged_heads = merged(gitpython, target)
        return [
            cls(
                branch,
                [
                    MaybeDeleteStep(
                        branch,
                        branch.commit,
                        lambda: branch in merged_heads,
                    )
                ],
            )
            for branch in branches
        ]

    @classmethod
    def rebased(cls, gitpython: git.Repo, target: git.Head) -> list["MaybeDeletePlan"]:
        branches = [head for head in gitpython.heads if head != target]
        return [
            cls(
                branch=branch,
                steps=[
                    MaybeDeleteStep(
                        branch,
                        branch.commit,
                        lambda: contains_equivalent(repo=gitpython, upstream=target, head=branch),
                    )
                ],
            )
            for branch in branches
        ]

    @classmethod
    def squashed(cls, gitpython: git.Repo, target: git.Head) -> list["MaybeDeletePlan"]:
        branches = [head for head in gitpython.heads if head != target]
        return [
            cls(
                branch=branch,
                steps=[
                    MaybeDeleteStep(
                        branch=branch,
                        commit=commit,
                        delete=lambda: is_squash_commit(gitpython, commit, diff),
                    )
                    for (commit, diff) in potential_squash_commits(gitpython, a=target, b=branch)
                ],
            )
            for branch in branches
        ]
