"""Errors raised by agy-call; each carries the CLI exit code in `code`."""


class AgyCallError(Exception):
    code = 1


class Running(AgyCallError):
    code = 25


class Mismatch(AgyCallError):
    code = 24


class Quota(AgyCallError):
    code = 10

    def __init__(self, key, until):
        super().__init__("quota for %s until %s" % (key, until.astimezone().strftime("%Y-%m-%d %H:%M %Z")))
        self.key, self.until = key, until


class Fatal(AgyCallError):
    def __init__(self, code, reason):
        super().__init__("agy-delegate exit %s: %s" % (code, reason))
        self.code, self.reason = code, reason


class GaveUp(AgyCallError):
    code = 20

    def __init__(self, findings, conversation=None, usage=None, job_dir=None):
        super().__init__("no usable reply within the turn limit: " + "; ".join(findings)[-2000:])
        self.findings, self.conversation, self.usage, self.job_dir = list(findings), conversation, usage or {}, job_dir


class CheckError(AgyCallError):
    code = 21


class NoConversation(AgyCallError):
    code = 23


class Cancelled(AgyCallError):
    code = 27
