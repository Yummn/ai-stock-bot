"""Cancellation for descendants of an owned worker only."""
import os

import psutil


def terminate_worker_tree(pid):
    if pid == os.getpid():
        raise ValueError("Refusing to terminate the current process")
    try:
        parent = psutil.Process(pid)
        if parent.ppid() != os.getpid():
            raise ValueError("Only a directly owned worker can be stopped")
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    for process in children + [parent]:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(children + [parent], timeout=3)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=3)
