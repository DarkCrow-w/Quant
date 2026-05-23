"""Memory-bounded helpers for large executor workloads."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Executor, Future, wait
from typing import Callable, Iterable, Iterator, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def bounded_futures(
    executor: Executor,
    items: Iterable[T],
    fn: Callable[[T], R],
    max_pending: int,
) -> Iterator[tuple[T, Future[R]]]:
    """Yield completed futures while keeping only ``max_pending`` submitted."""
    iterator = iter(items)
    pending: dict[Future[R], T] = {}

    def submit_one() -> bool:
        try:
            item = next(iterator)
        except StopIteration:
            return False
        pending[executor.submit(fn, item)] = item
        return True

    for _ in range(max(1, max_pending)):
        if not submit_one():
            break

    while pending:
        done, _ = wait(pending, return_when=FIRST_COMPLETED)
        for future in done:
            item = pending.pop(future)
            yield item, future
            submit_one()
