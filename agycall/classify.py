"""Turn one finished agy run into exactly one outcome (spec section 5.1).
Only agy's stderr and the envelope's status, error and denied_actions fields are searched; the reply text never is."""
import dataclasses
import json
import re

PARTIAL = re.compile(r"print timeout after .*returning partial output", re.IGNORECASE)
TIMEOUT_WORDS = ("timed out", "deadline exceeded")
RULES = (  # checked in this order against the envelope's error followed by stderr, case-insensitively
    ("denied", ("user denied permission", "permission check failed", "auto-denied", "permission that headless")),
    ("output-limit", ("exceeded the output token limit",)),
    ("quota", ("quota", "rate limit", "resource exhausted")),
    ("auth", ("unauthenticated", "unauthorized", "sign in", "please authenticate", "reauth")),
    ("model", ("invalid --model", "is not recognized as a known model", "not a known model")),
)
USAGE_KEYS = {"input_tokens": "input", "output_tokens": "output", "thinking_tokens": "thinking",
              "cache_read_tokens": "cache_read", "total_tokens": "total"}


@dataclasses.dataclass
class Outcome:
    name: str            # cancelled, timeout, denied, output-limit, quota, auth, model, malformed, reply, reply-with-error, empty
    response: str        # the envelope's reply text, "" when there is none
    conversation: object # the envelope's conversation id, or None
    usage: dict          # token counts under input, output, thinking, cache_read, total
    denied: list         # action kinds from denied_actions, for example ["command"]
    status: object       # the envelope's status string, or None
    exit: object         # agy's exit status (negative for a signal), or None when agy-call stopped the run
    reason: str          # one line for findings and error messages


def envelope(out):
    """agy's JSON envelope, or None when stdout is not one JSON object with a string status and a string reply."""
    try:
        env = json.loads(out.decode("utf-8", "replace"), strict=False)  # agy has put raw newlines inside strings
    except ValueError:
        return None
    if not isinstance(env, dict) or not isinstance(env.get("status"), str) or not isinstance(env.get("response", ""), str):
        return None
    return env


def _line(text, needle):
    for line in text.splitlines():
        if needle in line.lower():
            return line.strip()[:300]
    return needle


def classify(exit, out, err, stopped=None):
    env = envelope(out)
    e = env or {}
    response = e.get("response") or ""
    usage = e.get("usage") if isinstance(e.get("usage"), dict) else {}
    usage = {USAGE_KEYS[k]: v for k, v in usage.items() if k in USAGE_KEYS and isinstance(v, (int, float))}
    denied = [d["action"] for d in e.get("denied_actions") or [] if isinstance(d, dict) and d.get("action")]
    text = "%s\n%s" % (e.get("error") or "", err)
    low = text.lower()

    def outcome(name, reason):
        return Outcome(name, response, e.get("conversation_id") or None, usage, denied, e.get("status"), exit, reason)

    if stopped == "cancel":
        return outcome("cancelled", "cancelled by request")
    if stopped == "deadline":
        return outcome("timeout", "agy-call stopped agy at its deadline")
    m = PARTIAL.search(err)
    if m:
        return outcome("timeout", m.group(0)[:300])
    for word in TIMEOUT_WORDS:
        if word in low:
            return outcome("timeout", _line(text, word))
    if denied:
        return outcome("denied", "agy refused a %s action: headless mode cannot ask for permission" % "/".join(denied))
    for name, needles in RULES:
        for needle in needles:
            if needle in low:
                return outcome(name, _line(text, needle))
    if env is None:
        if isinstance(exit, int) and exit < 0:
            return outcome("malformed", "agy was killed by signal %d" % -exit)
        return outcome("malformed", "agy's output was not a JSON envelope (agy exited %s)" % exit)
    if response.strip():
        if exit == 0 and env["status"] == "SUCCESS":
            return outcome("reply", "agy replied")
        first = (str(e.get("error") or "").splitlines() or ["exit %s" % exit])[0]
        return outcome("reply-with-error", "agy reported %s: %s" % (env["status"], first[:300]))
    return outcome("empty", "agy returned no reply")
