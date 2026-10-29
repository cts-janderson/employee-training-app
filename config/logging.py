import logging
import re

# Verification, setup and reset links carry a token in the path. Request and access logs must not keep it.
ACCOUNT_LINK = re.compile(r"(/account/(?:verify|reset|setup)/)[^/\s\"?]+/[^/\s\"?]+/?")


def redact_account_links(text):
    return ACCOUNT_LINK.sub(r"\1[redacted]/", text)


class RedactAccountLinks(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        redacted = redact_account_links(message)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True
