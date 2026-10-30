from gunicorn.glogging import Logger

from config.logging import RedactAccountLinks


class RedactingLogger(Logger):
    """Gunicorn's access and error logs, without the account-link tokens Django's own logs already hide."""

    def setup(self, cfg):
        super().setup(cfg)
        for log in (self.access_log, self.error_log):
            if not any(isinstance(item, RedactAccountLinks) for item in log.filters):
                log.addFilter(RedactAccountLinks())
