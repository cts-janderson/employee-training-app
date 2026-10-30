from .accounts import is_account_administrator, pending_applications
from .dev_auth import demo_enabled
from .sign_in_protection import open_block_count


def account_navigation(request):
    context = {"demo_enabled": demo_enabled()}
    employee = getattr(request, "employee", None)
    # Only real administrators pay for these queries; the counts point them at work waiting on Accounts.
    if employee is not None and is_account_administrator(employee):
        context["waiting_applications"] = pending_applications().count()
        context["open_blocks"] = open_block_count()
    return context
