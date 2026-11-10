"""The job store: one folder per job, atomic files, locks outside the movable job folder."""
import contextlib
import fcntl
import hashlib
import json
import os
import re
import tempfile

JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\Z")


class Busy(Exception):
    pass


def home():
    return os.path.expanduser(os.environ.get("AGYCALL_HOME") or "~/.agy-call")


def store_dir(store=None, namespace=None):
    if store:
        return os.path.abspath(os.path.expanduser(store))
    return os.path.join(home(), "jobs", namespace or "default")


def write_atomic(path, data):
    if not isinstance(data, str):
        data = json.dumps(data, indent=1, ensure_ascii=False)
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


@contextlib.contextmanager
def flock(path, blocking=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "a")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except BlockingIOError:
        fh.close()
        raise Busy(path)
    try:
        yield fh
    finally:
        fh.close()  # closing releases the lock unless a child inherited the descriptor


def identity(prompt, key, cwd, dirs, seed, check_identity, mode):
    blob = json.dumps([prompt, key, cwd, list(dirs or []), seed, check_identity, mode], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Job:
    def __init__(self, store, job_id):
        if not isinstance(job_id, str) or not JOB_ID.match(job_id):
            raise ValueError("job id must match [A-Za-z0-9._-]{1,120}: %r" % (job_id,))
        self.store, self.id = store, job_id
        self.dir = os.path.join(store, job_id)
        self.lock_path = os.path.join(store, ".locks", job_id + ".lock")

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def _names(self):
        d = self.path("attempts")
        return os.listdir(d) if os.path.isdir(d) else []

    def launched(self):
        """Attempt folders that were launched (or meant to be), oldest first."""
        return sorted((self.path("attempts", n) for n in self._names() if re.fullmatch(r"\d{3,}", n)), key=lambda p: int(os.path.basename(p)))

    def new_attempt(self):
        while True:
            used = [int(n.split(".")[0]) for n in self._names() if re.fullmatch(r"\d{3,}(\.unlaunched)?", n)]
            p = self.path("attempts", "%03d" % (max(used, default=0) + 1))
            try:
                os.makedirs(p)
                return p
            except FileExistsError:
                continue

    def status(self):
        return read_json(self.path("status.json"), {}) or {}

    def set_status(self, **kw):
        st = self.status()
        st.update(kw)
        write_atomic(self.path("status.json"), st)
        return st

    def archive(self):
        vdir = os.path.join(self.store, ".versions", self.id)
        os.makedirs(vdir, exist_ok=True)
        n = 1
        while os.path.exists(os.path.join(vdir, "v%d" % n)):
            n += 1
        dest = os.path.join(vdir, "v%d" % n)
        os.rename(self.dir, dest)
        return dest
