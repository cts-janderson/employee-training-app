import logging
import re
from urllib.parse import unquote

# Verification, setup and reset links carry a token in the path. Request and access logs must not keep it.
ACCOUNT_LINK = re.compile(r"(/account/(?:verify|reset|setup)/)[^/\s\"?]+/[^/\s\"?]+/?")
# Each path-like run with its query, matched once from its first slash so a line of slashes costs linear time.
PATH = re.compile(r"/[^\s\"]*")


def _redact_encoded(match):
    # Django routes /account/%76erify/... to the same views. Replace the whole path rather than logging its decoded
    # form, which could carry a newline into the log.
    path = match.group()
    return "/account/[redacted]/" if "%" in path and ACCOUNT_LINK.search(unquote(path)) else path


def redact_account_links(text):
    return PATH.sub(_redact_encoded, ACCOUNT_LINK.sub(r"\1[redacted]/", text))


class RedactAccountLinks(logging.Filter):
    def filter(self, record):
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_account_links(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_account_links(record.stack_info)
        message = record.getMessage()
        redacted = redact_account_links(message)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True
