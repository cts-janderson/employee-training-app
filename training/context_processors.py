from .dev_auth import demo_enabled


def account_navigation(request):
    return {"demo_enabled": demo_enabled()}
