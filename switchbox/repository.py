import dataclasses
import logging
import pathlib
import typing

import click
import git

logger = logging.getLogger(__name__)

F = typing.TypeVar("F", bound=typing.Callable)
Named = typing.TypeVar("Named", git.Head, git.Remote)

SECTION = "switchbox"


@dataclasses.dataclass(frozen=True)
class GitOption:
    section: str
    option: str
    value: str

    def __str__(self) -> str:
        return f"{self.section}.{self.option}={self.value}"


@dataclasses.dataclass(frozen=True)
class Config:
    mainline_names: typing.Sequence[str] = ("main", "master")
    upstream_names: typing.Sequence[str] = ("upstream", "origin")
    write_config: bool = True
    application: str = "switchbox"


class RepositoryException(click.ClickException):
    pass


@dataclasses.dataclass(frozen=True)
class Repository:
    repo: git.Repo
    config: Config

    @property
    def path(self) -> pathlib.Path:
        if working_tree_dir := self.repo.working_tree_dir:
            return pathlib.Path(working_tree_dir)

        return pathlib.Path(self.repo.git_dir)

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
        return self.repo.active_branch.name

    @property
    def mainline(self) -> str:
        if mainline := self.get("mainline"):
            return mainline

        return self.detect_mainline()

    @property
    def upstream(self) -> str:
        if upstream := self.get("upstream"):
            return upstream

        return self.detect_upstream()

    @property
    def section(self) -> str:
        return self.config.application

    def detect_mainline(self) -> str:
        if not self.repo.heads:
            raise RepositoryException(
                "Repository has no branches. Is this a new repository?"
            )

        if head := self._first_match(self.repo.heads, self.config.mainline_names):
            self.set("mainline", head.name)
            return head.name

        raise RepositoryException(f"Could not find a mainline branch for {self}.")

    def detect_upstream(self) -> str:
        if not self.repo.heads:
            raise RepositoryException(
                "Repository has no remotes. Is this a new repository?"
            )

        if remote := self._first_match(self.repo.remotes, self.config.upstream_names):
            self.set("upstream", remote.name)
            return remote.name

        raise RepositoryException(f"Could not find an upstream remote for {self}.")

    def get(self, option: str) -> typing.Optional[str]:
        with self.repo.config_reader() as reader:
            if reader.has_section(SECTION):
                if reader.has_option(SECTION, option):
                    value = reader.get_value(SECTION, option)

                    if isinstance(value, float):
                        raise RepositoryException(
                            "Unexpected value for {}.{}: {!r}".format(
                                SECTION,
                                option,
                                value,
                            )
                        )

                    return value
        return None

    def set(self, option: str, value: str) -> GitOption:
        with self.repo.config_writer("repository") as writer:
            if not writer.has_section(SECTION):
                writer.add_section(SECTION)
            writer.set(SECTION, option, value)

        return GitOption(SECTION, option, value)

    def options(self) -> typing.Sequence[GitOption]:
        with self.repo.config_reader() as reader:
            if not reader.has_section(SECTION):
                return []
            return [
                GitOption(SECTION, option, value)
                for option, value in reader.items(SECTION)
            ]

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
        self.repo.git._call_process(
            "remote",
            "update",
            insert_kwargs_after="update",
            prune=True,
        )

    def update_branch_from_remote(self, remote: str, branch: str) -> None:
        self.repo.git.branch(branch, f"{remote}/{branch}", force=True)
        # self.repo.git.fetch(remote, f"{branch}:{branch}", "--update-head-ok")

    def switch(self, mainline: str) -> None:
        self.repo.git.switch(mainline)

    def discover_merged_branches(self, target: str) -> typing.Set[str]:
        """Find branches merged into a target branch."""
        rc, stdout, stderr = self.repo.git.branch(
            "--list",
            "--format=%(refname:short)",
            "--merged",
            target,
            with_extended_output=True,
        )
        return {line.lstrip() for line in stdout.splitlines()} - {target}

    def remove_branch(self, branch: str, *, dry_run: bool = False) -> None:
        if self.repo.active_branch.name == branch:
            logger.debug("Skipping active branch %s", branch)
        elif dry_run:
            logger.info("Would delete branch %s", branch)
        else:
            logger.info("Deleting branch %s", branch)
            self.repo.delete_head(branch)
