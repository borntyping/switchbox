import typing

import rich.progress

T = typing.TypeVar("T")


def advance(
    items: typing.Sequence[T],
    progress: rich.progress.Progress,
    task: rich.progress.TaskID,
) -> typing.Iterator[T]:
    progress.update(task, total=len(items))
    for item in items:
        progress.update(task, item=item)
        yield item
        progress.advance(task)
