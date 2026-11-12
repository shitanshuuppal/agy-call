"""Builders for tests/fake_agy.py turns, one per shape of agy run the classifier knows."""

CUT_OFF = ("Your previous response was cut off because it exceeded the output token limit\n"
           "Please continue from where you left off, keeping your response shorter\nRetries remaining: 3")
DENIED_NOTICE = ('jetski: no output produced, a tool required the "command" permission that headless mode cannot '
                 "prompt for, so it was auto-denied.\n")


def reply(text, conv="c1", **extra):
    return {"response": text, "conversation": conv, **extra}


def error_reply(text, error=CUT_OFF, conv="c1"):
    return {"exit": 1, "status": "ERROR", "response": text, "error": error, "conversation": conv}


def empty(conv=None):
    return {"response": "", "conversation": conv}


def denied(kind="command", conv="c1"):
    return {"response": "", "conversation": conv, "stderr": DENIED_NOTICE,
            "denied_actions": [{"action": kind, "display_name": "RunCommand"}]}


def partial(text, conv="c1"):
    return {"response": text, "conversation": conv, "stderr": "print timeout after 10m0s, returning partial output\n"}


def quota_error(reset="Resets in 10m"):
    return {"exit": 1, "status": "ERROR", "response": "", "error": "RESOURCE_EXHAUSTED: quota exceeded for this model. " + reset}


def auth_error():
    return {"exit": 1, "raw_hex": "", "stderr": "Error: unauthenticated; run agy once to sign in\n"}


def model_error(name="m"):
    return {"exit": 1, "raw_hex": "", "stderr": 'Error: invalid --model "%s"\n' % name}


def malformed(raw=b"", stderr="flag provided but not defined: -zzz\n", exit=2):
    return {"exit": exit, "raw_hex": raw.hex(), "stderr": stderr}
