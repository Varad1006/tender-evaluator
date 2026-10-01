"""Background work (reading a tender or a bid) on a small, fixed pool.

Ten bid envelopes uploaded together must not become ten simultaneous 200-page parses: they queue,
two at a time, and each keeps its database transactions short. Tests run jobs inline
(TE_JOBS_INLINE=1) so a request returns with its job done.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)
_POOL = ThreadPoolExecutor(max_workers=int(os.environ.get("TE_JOB_WORKERS", "2")), thread_name_prefix="te-job")


def _run(fn, *args):
    try:
        fn(*args)
    except Exception:  # pragma: no cover - each job records its own failure
        log.exception("Background job %s%s failed", getattr(fn, "__name__", fn), args)


def submit(fn, *args) -> None:
    if os.environ.get("TE_JOBS_INLINE"):
        _run(fn, *args)
    else:
        _POOL.submit(_run, fn, *args)
