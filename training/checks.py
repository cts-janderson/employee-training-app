from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security, deploy=True)
def trusted_proxy_networks(app_configs, **kwargs):
    if settings.TRUSTED_PROXY_NETWORKS:
        return []
    return [Warning(
        "TRUSTED_PROXY_NETWORKS is empty, so behind a reverse proxy every client shares the proxy's rate limits.",
        hint="Set it to the reverse proxy's network; see docs/plans/30-production-deployment.md.",
        id="training.W001",
    )]
