from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import INTERNAL_RESET_SESSION_TOKEN
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views.decorators.http import require_http_methods

from .accounts import (
    AccountInputError,
    AccountState,
    account_state,
    awaiting_setup,
    decide_application,
    invite_employee,
    mark_verified,
    normalize_email,
    pending_applications,
    rate_limited,
    redeem_link,
    register,
    request_password_reset,
    resend_setup,
    resend_verification,
    reset_tokens,
    setup_tokens,
    verification_tokens,
    verify_email,
)
from .dev_auth import account_admin_required
from .models import Employee

INVALID_LINK = "This link is invalid or has expired. Request a new one."


def _client(request):
    return request.META.get("REMOTE_ADDR", "")


def _message(request, title, message, *, status=200, link=None, link_text=""):
    return render(request, "training/account_message.html", {
        "title": title, "message": message, "link": link, "link_text": link_text,
    }, status=status)


def _too_many(request):
    return _message(request, "Try again later", "Too many requests. Wait a while and try again.", status=429)


class SignInView(auth_views.LoginView):
    template_name = "training/sign_in.html"
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        if rate_limited("sign_in", f"{_client(request)}:{normalize_email(request.POST.get('username'))}"):
            # An unbound form, so the limited attempt never reaches authentication.
            form = self.get_form_class()(request)
            return self.render_to_response(self.get_context_data(form=form, limited=True), status=429)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        # A real session replaces any demo choice.
        self.request.session.pop("demo_employee_id", None)
        return response


@require_http_methods(["GET", "POST"])
def register_view(request):
    error = None
    if request.method == "POST":
        if rate_limited("register", _client(request)):
            return _too_many(request)
        try:
            register(name=request.POST.get("name", ""), email=request.POST.get("email", ""),
                     password=request.POST.get("password", ""))
        except AccountInputError as exc:
            error = exc
        else:
            return _message(request, "Check your email",
                            "If the address can be registered, we've sent a link to confirm it. "
                            "After you confirm it, an administrator reviews your application.")
    return render(request, "training/register.html", {
        "error": error, "error_field": getattr(error, "field", None) or "email",
        "form_name": request.POST.get("name", ""), "form_email": request.POST.get("email", ""),
    }, status=400 if error else 200)


def _link_employee(uidb64):
    try:
        return Employee.objects.filter(pk=force_str(urlsafe_base64_decode(uidb64)), is_demo=False,
                                       is_active=True).first()
    except (TypeError, ValueError, OverflowError, ValidationError):
        return None


@require_http_methods(["GET", "POST"])
def verify_email_view(request, uidb64, token):
    employee = _link_employee(uidb64)
    if employee is None or not verification_tokens.check_token(employee, token):
        return _message(request, "Link unavailable", INVALID_LINK, status=400)
    if request.method == "POST":
        if verify_email(employee.pk, token) is None:
            return _message(request, "Link unavailable", INVALID_LINK, status=400)
        return _message(request, "Email confirmed",
                        "Your email address is confirmed. Sign in to see the status of your account.",
                        link=reverse("sign_in"), link_text="Sign in")
    # Confirming takes a POST so that link scanners can't confirm on the employee's behalf.
    return render(request, "training/verify_email.html", {"employee": employee})


@login_required
@require_http_methods(["GET", "POST"])
def account_status(request):
    employee = request.user
    request.employee = employee
    notice = ""
    if request.method == "POST" and request.POST.get("action") == "resend_verification":
        if rate_limited("account_email", f"verify:{employee.pk}"):
            return _too_many(request)
        resend_verification(employee)
        notice = "We've sent a new confirmation link."
    return render(request, "training/account_status.html", {
        "state": account_state(employee), "states": AccountState, "notice": notice,
    })


@require_http_methods(["GET", "POST"])
def password_reset_request(request):
    if request.method == "POST":
        email = normalize_email(request.POST.get("email"))
        if rate_limited("account_email", f"reset:{_client(request)}:{email}"):
            return _too_many(request)
        request_password_reset(email)
        return _message(request, "Check your email",
                        "If an account uses that address, we've sent a link to reset its password.")
    return render(request, "training/password_reset_request.html")


class _AccountLinkView(auth_views.PasswordResetConfirmView):
    """Reuses Django's link-then-form flow, which keeps the token out of the Referer header."""
    template_name = "training/set_password.html"
    title = "Choose a password"
    done_message = ""

    def eligible(self, employee):
        return not employee.is_demo and employee.is_active

    def get_user(self, uidb64):
        employee = super().get_user(uidb64)
        return employee if employee is not None and self.eligible(employee) else None

    def after_password(self, employee):
        pass

    def form_valid(self, form):
        token = self.request.session.get(INTERNAL_RESET_SESSION_TOKEN)
        with transaction.atomic():
            employee = redeem_link(self.user.pk, self.token_generator, token)
            if employee is None or not self.eligible(employee):
                return _message(self.request, "Link unavailable", INVALID_LINK, status=400)
            employee.set_password(form.cleaned_data["new_password1"])
            employee.save(update_fields=["password"])
            self.after_password(employee)
        self.request.session.pop(INTERNAL_RESET_SESSION_TOKEN, None)
        return _message(self.request, "Password saved", self.done_message,
                        link=reverse("sign_in"), link_text="Sign in")


class PasswordResetConfirm(_AccountLinkView):
    token_generator = reset_tokens
    done_message = "Your new password is saved. Other signed-in sessions have ended."


class AccountSetup(_AccountLinkView):
    token_generator = setup_tokens
    done_message = "Your account is set up. Sign in to see your training."

    def eligible(self, employee):
        return (super().eligible(employee) and employee.approval_status == Employee.Approval.APPROVED
                and not employee.has_usable_password())

    def after_password(self, employee):
        # Opening the setup link proves control of the mailbox.
        mark_verified(employee)


@method_decorator(login_required, name="dispatch")
class PasswordChange(auth_views.PasswordChangeView):
    template_name = "training/password_change.html"
    success_url = reverse_lazy("password_change_done")


@login_required
def password_change_done(request):
    return _message(request, "Password changed", "Your new password is saved. Other signed-in sessions have ended.",
                    link=reverse("account_status"), link_text="Back to your account")


@require_http_methods(["GET", "POST"])
@account_admin_required
def account_admin(request):
    error = None
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "invite":
                invite_employee(actor=request.employee, name=request.POST.get("name", ""),
                                email=request.POST.get("email", ""))
            elif action in {"approve", "reject"}:
                decide_application(actor=request.employee, employee_id=request.POST.get("employee_id"),
                                   approve=action == "approve")
            elif action == "resend_setup":
                resend_setup(actor=request.employee, employee_id=request.POST.get("employee_id"))
            else:
                error = AccountInputError("Choose an action.")
        except AccountInputError as exc:
            error = exc
        if not error:
            return redirect("account_admin")
    return render(request, "training/account_admin.html", _admin_context(
        error=error, form_name=request.POST.get("name", "") if error else "",
        form_email=request.POST.get("email", "") if error else "",
    ), status=400 if error else 200)


def _admin_context(**extra):
    return {
        "applications": pending_applications(),
        "invited": awaiting_setup(),
        "recent": Employee.objects.filter(is_demo=False, approval_decided_at__isnull=False)
                  .select_related("approval_decided_by").order_by("-approval_decided_at")[:20],
        **extra,
    }
