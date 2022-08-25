import unittest.mock

import git

from switchbox.repository import Repository


def test_first_match():
    repo = unittest.mock.Mock()
    a, b, c = git.Remote(repo, "a"), git.Remote(repo, "b"), git.Remote(repo, "c")
    b = git.Remote(repo, "b")
    c = git.Remote(repo, "c")
    assert Repository._first_match([a, b, c], ["b", "absent"]) is b
