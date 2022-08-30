switchbox
=========

A collection of small tools for git workflows.

Installation
------------

Clone the repository and install the package with `pip`.

```zsh
pip install --user .
```

Usage
-----

Invoke `switchbox` directly or run it via `git switchbox`.

Switchbox commands assume your git repository has a "mainline" branch and an
"upstream" remote. When Switchbox is used for the first time (or you run
`switchbox setup`) it will find and remember names for these.

* Mainline will default to a branch named `main` or `master`.
* Upstream will default to a remote named `upstream` or `origin`.

Switchbox options are set in a repository's `.git/config` file under a
`switchbox` section.

### `switchbox config`

Show config options that Switchbox has set.

### `switchbox config init`

Detect a mainline branch and upstream remote, and save them to the repositories
git configuration. This will be done automatically when you first use a command
that works on a mainline branch or upstream remote.

### `switchbox config mainline $branch`

Change the mainline branch.

### `switchbox config upstream $remote`

Change the upstream remote.

### `switchbox finish [--update/--no-update]`

* Update all git remotes.
* Update the local mainline branch to match the remote mainline branch.
* Switch to the mainline branch.
* Remove branches **merged** into the mainline branch.
* Remove branches **squashed** into the mainline branch.

### `switchbox tidy [--update/--no-update]`

* Update all git remotes.
* Remove branches **merged** into the mainline branch.
* Remove branches **squashed** into the mainline branch.

### `switchbox update`

* Update all git remotes.
