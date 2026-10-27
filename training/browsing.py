from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from django.core.paginator import Paginator
from django.db.models import Q
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


PAGE_SIZE = 25


@dataclass(frozen=True)
class Browse:
    page: object
    total: int
    query: str
    params: dict
    path: str

    def page_url(self, number):
        return f"{self.path}?{urlencode({**self.params, 'page': number})}"

    def return_to(self):
        return self.page_url(self.page.number)

    @property
    def previous_url(self):
        return self.page_url(self.page.previous_page_number()) if self.page.has_previous() else ""

    @property
    def next_url(self):
        return self.page_url(self.page.next_page_number()) if self.page.has_next() else ""


def browse(request, queryset, *, search_fields, extra_params=None):
    query = " ".join(request.GET.get("q", "").split())[:200]
    if query and search_fields:
        expression = Q()
        for field in search_fields:
            expression |= Q(**{f"{field}__icontains": query})
        queryset = queryset.filter(expression).distinct()
    page = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get("page"))
    return Browse(page, page.paginator.count, query, {**(extra_params or {}), "q": query}, request.path)


def safe_return_to(request, list_name):
    value = request.GET.get("return_to", "")
    prefix = reverse(list_name)
    if (value and url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()})
            and urlsplit(value).path == prefix):
        return value
    return prefix
