import dataclasses
import logging
import pathlib
import typing

import click
import git

logger = logging.getLogger(__name__)

F = typing.TypeVar("F", bound=typing.Callable)
Named = typing.TypeVar("Named", git.Head, git.Remote)


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
    click: click.Context

    def __str__(self) -> str:
        if working_tree_dir := self.repo.working_tree_dir:
            path = pathlib.Path(working_tree_dir)
        else:
            path = pathlib.Path(self.repo.git_dir)

        home = pathlib.Path.home()
        try:
            return f"~/{path.relative_to(home)}"
        except ValueError:
            return f"{path}"

    @property
    def mainline(self) -> str:
        return self.detect_mainline()

    @mainline.setter
    def mainline(self, value: str) -> None:
        self.set("mainline", value)

    @property
    def upstream(self) -> str:
        return self.detect_upstream()

    @upstream.setter
    def upstream(self, value: str) -> None:
        self.set("upstream", value)

    def detect_mainline(self) -> str:
        if mainline := self.get("mainline"):
            return mainline

        if not self.repo.heads:
            raise RepositoryException(
                "Repository has no branches. Is this a new repository?"
            )

        if head := self._first_match(self.repo.heads, self.config.mainline_names):
            self.set("mainline", head.name)
            return head.name

        raise RepositoryException(f"Could not find a mainline branch for {self}.")

    def detect_upstream(self) -> str:
        if upstream := self.get("upstream"):
            return upstream

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
            if reader.has_section(self.config.application):
                if reader.has_option(self.config.application, option):
                    value = reader.get_value(self.config.application, option)

                    if isinstance(value, float):
                        raise RepositoryException(
                            "Unexpected value for {}.{}: {!r}".format(
                                self.config.application,
                                option,
                                value,
                            )
                        )

                    return value
        return None

    def set(self, option: str, value: str) -> None:
        with self.repo.config_writer("repository") as writer:
            if not writer.has_section(self.config.application):
                writer.add_section(self.config.application)

            writer.set(self.config.application, option, value)

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

    def mainline_is_active_branch(self) -> bool:
        return self.repo.active_branch.name == self.mainline

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
