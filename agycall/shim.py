"""Capture shim. Installed as an executable named `agy` first on agy-delegate's PATH, it runs the real agy
(AGYCALL_REAL_AGY) with the same arguments, writes its stdout and stderr straight into AGYCALL_ATTEMPT/agy-N.out and agy-N.err (agy-N.args records the arguments), then copies them to its own stdout and stderr after agy exits.
This file must stay standard-library only and import nothing from the package: it runs as a standalone script."""
import json
import os
import subprocess
import sys


def _open_set(folder):
    i = 1
    while True:
        try:
            fd = os.open(os.path.join(folder, "agy-%d.args" % i), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            break
        except FileExistsError:
            i += 1
    with os.fdopen(fd, "w") as fh:
        json.dump(sys.argv[1:], fh)
    out = open(os.path.join(folder, "agy-%d.out" % i), "wb")
    try:
        return out, open(os.path.join(folder, "agy-%d.err" % i), "wb")
    except OSError:
        out.close()
        raise


def _copy(path, dst):
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                return
            dst.write(chunk)
            dst.flush()


def main():
    real = os.environ.get("AGYCALL_REAL_AGY")
    if not real:
        sys.stderr.write("agy-call shim: AGYCALL_REAL_AGY is not set\n")
        return 127
    paths = None
    folder = os.environ.get("AGYCALL_ATTEMPT")
    if folder:
        try:
            paths = _open_set(folder)
        except OSError as e:
            sys.stderr.write("agy-call shim: cannot save agy streams: %s\n" % e)
    if paths is None:
        return subprocess.call([real] + sys.argv[1:], close_fds=False)
    # agy writes straight into the attempt folder, so a kill of the shim or agy loses nothing already written;
    # close_fds=False lets agy inherit the lock descriptors that keep the job and conversation locked
    with paths[0] as out, paths[1] as err:
        rc = subprocess.call([real] + sys.argv[1:], stdout=out, stderr=err, close_fds=False)
    _copy(paths[0].name, sys.stdout.buffer)
    _copy(paths[1].name, sys.stderr.buffer)
    return rc if rc >= 0 else 128 - rc


def install(folder):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "agy")
    with open(__file__, encoding="utf-8") as fh:
        src = fh.read()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!%s\n" % sys.executable + src)
    os.chmod(path, 0o755)
    return folder


def env(base, bin_dir, attempt, real_agy):
    return dict(base, PATH=bin_dir + os.pathsep + base.get("PATH", ""), AGYCALL_ATTEMPT=attempt, AGYCALL_REAL_AGY=real_agy)


if __name__ == "__main__":
    sys.exit(main())
