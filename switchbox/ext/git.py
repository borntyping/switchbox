import logging
import typing

import git

logger = logging.getLogger(__name__)


class GitException(Exception):
    pass


class NoMergeBase(GitException):
    pass


class MultipleMergeBases(GitException):
    pass


def list_merged_heads(repo: git.Repo, into: git.Head) -> typing.Sequence[git.Head]:
    rc, stdout, stderr = repo.git.branch(
        "--list",
        "--format=%(refname:short)",
        "--merged",
        into.name,
        with_extended_output=True,
    )
    return [repo.heads[line.lstrip()] for line in stdout.splitlines()]


def find_merge_base(
    repo: git.Repo,
    a: git.refs.Head,
    b: git.refs.Head,
) -> git.objects.commit.Commit:
    merge_bases = repo.merge_base(a, b)

    if len(merge_bases) == 0:
        raise NoMergeBase("No merge base found for %(a).7s and %(b).7s" % {"a": a, "b": b})

    if len(merge_bases) >= 2:
        raise MultipleMergeBases("Multiple merge bases found for %(a).7s and %(b).7s" % {"a": a, "b": b})

    merge_base = merge_bases[0]

    if not isinstance(merge_base, git.objects.commit.Commit):
        raise GitException(f"Unknown merge base type: {merge_base!r}")

    return merge_base


def contains_equivalent(
    repo: git.Repo,
    upstream: git.refs.Head,
    head: git.refs.Head,
) -> bool:
    """
    Return True if all commits in <head> have an equivalent in <upstream>.
    https://git-scm.com/docs/git-cherry
    """
    logger.info(
        "Checking if '%(upstream)s' contains all commits from '%(head)s'",
        {"upstream": upstream, "head": head},
    )
    stdout = repo.git.cherry(upstream, head)
    return all(line[0] == "-" for line in stdout.splitlines())


def commits(
    repo: git.Repo,
    r1: git.objects.Commit | git.refs.Head,
    r2: git.objects.Commit | git.refs.Head,
) -> list[git.objects.Commit]:
    return list(repo.iter_commits(f"{r1}..{r2}"))


def potential_squash_commits(
    repo: git.Repo,
    a: git.refs.Head,
    b: git.refs.Head,
) -> typing.Sequence[tuple[git.Commit, git.DiffIndex]]:
    if a == b:
        logger.debug("Not checking for squash commits, branches are identical")
        return []

    logger.info("Checking if '%(b)s' was squashed into '%(a)s'", {"a": a, "b": b})

    try:
        merge_base = find_merge_base(repo, a, b)
    except NoMergeBase:
        return []

    diff = b.commit.diff(merge_base)

    if len(commits(repo, r1=merge_base, r2=b)) == 1:
        logger.info("Skipping branch with one commit, " "squashing and rebasing are equivalent in this case")
        return []

    return [(commit, diff) for commit in commits(repo, r1=merge_base, r2=a)]


def is_squash_commit(repo: git.Repo, commit: git.Commit, diff: git.DiffIndex):
    """
    Check if a commit matches a given diff.
    """
    if len(commit.parents) == 0:
        logger.debug("Skipping commit with no parents %(c)s", {"c": commit})
        return False
    elif len(commit.parents) >= 2:
        logger.debug("Skipping merge commit %(c)s", {"c": commit})
        return False
    else:
        parent = commit.parents[0]

    return commit.diff(parent) == diff


def contains_squash_commit(
    repo: git.Repo,
    a: git.refs.Head,
    b: git.refs.Head,
) -> bool | None:
    """
    Checks if B has been merged into A with a squash commit.

    This works by finding the common ancestor / merge base M, and checking if the diff
    of (B, M) matches a commit in M..A.
    """
    for commit, diff in potential_squash_commits(repo, a, b):
        logger.info(
            "Checking if '%(b)s' was squashed into '%(a)s' by %(c).7s",
            {"a": a, "b": b, "c": commit},
        )
        if is_squash_commit(repo, commit, diff):
            return True

    return False
