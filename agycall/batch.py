"""Many jobs at once; no new launch for a model key once a quota hold appears."""
import concurrent.futures as F
import os

from . import quota
from . import store as st
from .errors import Quota
from .runner import call


def _key(spec, common):
    return spec.get("model", common.get("model"))


def _done(spec, common):
    job = st.Job(st.store_dir(spec.get("store", common.get("store")), spec.get("namespace", common.get("namespace"))), spec["job"])
    return os.path.exists(job.path("answer.txt"))


def _run(spec, common):
    spec = dict(spec)
    job_id = spec.pop("job")
    prompt = spec.pop("prompt", None)
    prompt_file = spec.pop("prompt_file", None)
    if prompt is None:
        with open(prompt_file, encoding="utf-8") as fh:
            prompt = fh.read()
    return call(job_id, prompt, **{**common, **spec})


def batch(jobs, *, workers=4, **common):
    ids = [j["job"] for j in jobs]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError("duplicate job ids in batch: %s" % ", ".join(dupes))
    results, pending, flight = {}, list(jobs), {}
    with F.ThreadPoolExecutor(workers) as ex:
        while pending or flight:
            while pending and len(flight) < workers:
                spec = pending.pop(0)
                try:
                    if _done(spec, common) or {**common, **spec}.get("ignore_quota"):
                        until = None
                    else:
                        until = quota.held(_key(spec, common))
                except Exception as e:  # a bad row (invalid id, ...) is that row's result
                    results[spec["job"]] = e
                    continue
                if until:
                    results[spec["job"]] = Quota(_key(spec, common), until)
                    continue
                flight[ex.submit(_run, spec, common)] = spec["job"]
            if not flight:
                break
            done, _ = F.wait(flight, return_when=F.FIRST_COMPLETED)
            for f in done:
                job_id = flight.pop(f)
                try:
                    results[job_id] = f.result()
                except Exception as e:
                    results[job_id] = e
    return results
