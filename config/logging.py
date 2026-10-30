import logging
import re
from urllib.parse import unquote

# Verification, setup and reset links carry a token in the path. Request and access logs must not keep it.
ACCOUNT_LINK = re.compile(r"(/account/(?:verify|reset|setup)/)[^/\s\"?]+/[^/\s\"?]+/?")


def redact_account_links(text):
    redacted = ACCOUNT_LINK.sub(r"\1[redacted]/", text)
    # Django routes a percent-encoded path such as /account/%76erify/... to the same view, so check the decoded text.
    decoded = unquote(redacted)
    if ACCOUNT_LINK.search(decoded):
        return ACCOUNT_LINK.sub(r"\1[redacted]/", decoded)
    return redacted


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
