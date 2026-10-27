# Slice 15: search and browse growing lists (US-16)

Part of [Extended user stories](extended-stories.md). It depends on [Slice 13](13-assignment-report.md).

## Story

[US-16](../user-stories.md#us-16--search-and-browse-growing-lists): administrators search and page through trainings, quizzes, employees and assignments. Opening a result and coming back keeps the search, filters and page.

## Problem

Four administrator lists each build their own unpaged queryset:

| List | View | Today |
|---|---|---|
| Trainings | `training_list` in [views.py](../../training/views.py) | Every training, ordered by `created_at`, with every version prefetched |
| Library quizzes | `library_quiz_list` in [library_quiz_views.py](../../training/library_quiz_views.py) | Every library quiz, with `library_quiz_problems` worked out for each one |
| Employees | `employee_directory` in [employee_views.py](../../training/employee_views.py) | Every demo employee, ordered by `name` |
| Assignments | `assignment_dashboard` | Slice 13's `report_assignments` |

Adding a search box, a count, paging and query-string handling to each view separately would mean four copies of the same code. Each copy would get edge cases such as an out-of-range page or a blank search slightly wrong.

## Change

### 1. One browsing module

Add [training/browsing.py](../../training/browsing.py):

```python
PAGE_SIZE = 25

@dataclass(frozen=True)
class Browse:
    page: Page                 # django.core.paginator.Page
    total: int
    query: str                 # normalised search text ("" when none)
    params: dict               # every list parameter except "page", for building links
    def page_url(self, number) -> str: ...
    def return_to(self) -> str:   # the current list URL, including the page

def browse(request, queryset, *, search_fields, extra_params=None) -> Browse:
    """Apply the `q` search across search_fields (icontains, OR'd), count, and paginate.
    The queryset must already be ordered with a unique final key."""
```

- Use `Paginator.get_page()`. It clamps an invalid or out-of-range `page` to a valid page instead of returning 404, which suits a list someone returns to after records change.
- Normalise `q` by stripping it, collapsing whitespace and capping it at 200 characters.
- `extra_params` holds the filters each list already has (for the dashboard, `ReportFilters.as_params()`), so page links and return links keep them.
- **Authorization isn't checked here.** Each view passes a queryset that already applies its permission and archive rules, so search can't widen what's visible. That meets the fourth acceptance criterion by construction.

### 2. Each list uses it

| List | Ordering (unique final key) | `search_fields` |
|---|---|---|
| Trainings | latest version title, then `id` | the title of any version: annotate the latest title with `Subquery` for sorting and search `versions__title` with `.distinct()` |
| Library quizzes | `title`, `id` | `title` |
| Employees | `name`, `id` | `name`, `email` |
| Assignments | Slice 13's order | `employee__name`, `employee__email`, `version__title` |

- On the dashboard, the existing employee and training dropdowns stay. `q` is an extra filter. Add `q` to `ReportFilters` so the export (Slice 14) exports exactly what's searched. Then `report_rows` receives `browse.page.object_list`, and the follow-up summaries are computed for that page only.
- On the library quiz list, compute `library_quiz_problems` only for the quizzes on the current page.
- Remember that the employee directory view also handles the create-employee POST. Only the GET listing is paged. After a successful create, redirect to the list without parameters, as today.

### 3. Templates

Add a shared include, `templates/training/_browse_controls.html`, containing:

- a `<form method="get" role="search">` with a labelled search field and hidden inputs for `extra_params`;
- a count such as "Showing 26–50 of 112 employees", in a `<p aria-live="polite">`;
- previous and next links plus a "Page 2 of 5" label. Use `<nav aria-label="Pagination">` and `aria-current="page"`, and leave out links that don't apply.

Include it in the four list templates. With no search and at most one page, the list should look as it does today, apart from the count line.

### 4. Return to the list

Add `?return_to=<browse.return_to()>` to the links from each list row to its detail or editor page. The detail page's **Back** link uses `return_to` only if `url_has_allowed_host_and_scheme(return_to, allowed_hosts={request.get_host()})` passes **and** the URL starts with that list's path. Otherwise it falls back to today's link. Add this to the dashboard → assignment detail, employees → edit employee, trainings → draft editor, and quizzes → library quiz editor links. "When practical" in the story allows this: redirects after a POST on those pages don't have to carry `return_to`.

## Tests

`training/test_browsing.py`:

- `test_search_matches_any_listed_field_case_insensitively`.
- `test_total_counts_all_matches_not_just_the_page`.
- `test_out_of_range_page_shows_last_page`.
- `test_page_links_preserve_search_and_filters`.
- `test_search_does_not_reveal_archived_assignments_outside_the_archive_filter`.
- `test_employee_search_lists_only_demo_employees`.
- `test_return_link_rejects_foreign_hosts_and_other_paths`.
- `test_pages_do_not_repeat_or_skip_rows_with_equal_sort_keys`.

Existing list and dashboard tests must pass. Fixtures for those tests are smaller than one page.

## Out of scope

- Full-text search or ranking.
- Remembering searches across sessions.
- Learner-side search on My training.
