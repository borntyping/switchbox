import logging

import git

logger = logging.getLogger(__name__)


def find_merge_base(
    repo: git.Repo,
    a: git.refs.Head,
    b: git.refs.Head,
) -> git.objects.commit.Commit:
    merge_bases = repo.merge_base(a, b)

    if len(merge_bases) == 0:
        raise Exception(
            "No merge base found for %(a).7s and %(b).7s" % {"a": a, "b": b}
        )

    if len(merge_bases) >= 2:
        raise Exception(
            "Multiple merge bases found for %(a).7s and %(b).7s" % {"a": a, "b": b}
        )

    merge_base = merge_bases[0]

    if not isinstance(merge_base, git.objects.commit.Commit):
        raise Exception(f"Unknown merge base type: {merge_base!r}")

    return merge_base


def contains_squash_commit(
    repo: git.Repo,
    a: git.refs.Head,
    b: git.refs.Head,
) -> bool | None:
    """
    Checks if B has been merged into A with a squash commit.

    This works by finding the common ancestor / merge base M, and checking if
    """
    if a == b:
        logger.debug("Not checking for squash commits, branches are identical")
        return None

    merge_base = find_merge_base(repo, a, b)
    context = {"a": a, "b": b, "m": merge_base.hexsha[:7]}

    logger.info(
        "Checking if '%(b)s' was squashed into '%(a)s', merge_base=%(m)s",
        context,
    )

    branch_diff = b.commit.diff(merge_base)
    for commit in repo.iter_commits(f"{merge_base}...{a.name}"):
        if len(commit.parents) == 0:
            logger.debug("Skipping commit with no parents %(c)s", {"c": commit})
            continue
        elif len(commit.parents) >= 2:
            logger.debug("Skipping merge commit %(c)s", {"c": commit})
            continue
        else:
            parent = commit.parents[0]

        is_matching_squash_commit = commit.diff(parent) == branch_diff

        logger.info(
            "Checking if '%(b)s' was squashed into '%(a)s' by %(c).7s"
            "(merge_base=%(m).7s, is_matching_squash_commit=%(i)s)",
            {**context, "c": commit, "i": is_matching_squash_commit},
        )

        if is_matching_squash_commit:
            return True

    return False
