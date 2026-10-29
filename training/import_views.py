from io import BytesIO

from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.uploadhandler import FileUploadHandler, StopFutureHandlers
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_http_methods

from .dev_auth import admin_required
from .models import ImportBatch, Training
from .training_imports import (MAX_FILES, MAX_FILE_BYTES, MAX_REQUEST_BYTES, ImportInputError,
                               new_batch, parse_file, review_warnings, update_batch)


class BoundedTextUpload(FileUploadHandler):
    """Enforce stream bounds even when the declared file size cannot be trusted."""
    def __init__(self, request):
        super().__init__(request)
        self.total = self.count = self.current = 0

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.current = 0
        self.count += 1
        self.stream = BytesIO()
        self.rejected = False
        if self.count > MAX_FILES:
            self.reject("Choose at most five files.")
        raise StopFutureHandlers

    def reject(self, message):
        if not self.rejected:
            self.request.import_upload_errors = [*getattr(self.request, "import_upload_errors", []), message]
            self.rejected = True

    def receive_data_chunk(self, raw_data, start):
        self.current += len(raw_data)
        self.total += len(raw_data)
        if not self.rejected and (self.current > MAX_FILE_BYTES or self.total > MAX_FILES * MAX_FILE_BYTES):
            self.reject(f"{self.file_name}: files must be at most 1 MB, with at most five files per import.")
        if not self.rejected:
            self.stream.write(raw_data)
        return None

    def file_complete(self, file_size):
        self.stream.seek(0)
        upload = InMemoryUploadedFile(self.stream, self.field_name, self.file_name, self.content_type,
                                      file_size, self.charset, self.content_type_extra)
        upload.import_rejected = self.rejected
        return upload

    def upload_interrupted(self):
        if hasattr(self, "stream"):
            self.stream.close()


# Django's CSRF middleware normally parses uploads before the view. Install stream
# bounds first, then run the normal CSRF check before touching review state.
@csrf_exempt
@require_http_methods(["GET", "POST"])
@admin_required
def select_files(request):
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return HttpResponse("Invalid request length.", status=400)
    if length > MAX_REQUEST_BYTES:
        return render(request, "training/import_select.html", {
            "errors": ["The request is too large. Choose at most five files of 1 MB each."],
        }, status=413)
    request.upload_handlers.insert(0, BoundedTextUpload(request))
    return _select_files(request)


@csrf_protect
def _select_files(request):
    if request.method == "GET":
        return render(request, "training/import_select.html")
    files = request.FILES.getlist("files")
    errors = list(getattr(request, "import_upload_errors", []))
    if not files and not errors:
        errors.append("Select one to five Markdown or text files.")
    plan = []
    for upload in files:
        try:
            if not getattr(upload, "import_rejected", False):
                plan.append(parse_file(upload.name, upload.read(MAX_FILE_BYTES + 1), content_format="documents"))
        except ImportInputError as exc:
            errors.append(f"{upload.name}: {exc}")
        finally:
            upload.close()
    if plan:
        batch = new_batch(request.employee, plan)
        if errors:
            return _review_page(request, batch, errors=errors, status=400)
        return redirect("review_import", batch_id=batch.pk)
    return render(request, "training/import_select.html", {"errors": errors}, status=400)


def _review_page(request, batch, *, errors=None, submitted_titles=None, status=200):
    if batch.committed_at:
        results = Training.objects.filter(pk__in=batch.result_ids).with_display_title()
        return render(request, "training/import_done.html", {"batch": batch, "results": results})
    items = [{**item, "title": (submitted_titles or {}).get(item["id"], item["title"])} for item in batch.plan]
    for item in items:
        item["warnings"] = review_warnings(item)
        if item.get("content_format") == "documents":
            from .documents import render_body
            item["documents"] = [{**document, "preview": render_body(document["body"])}
                                 for document in item["documents"]]
    return render(request, "training/import_review.html", {"batch": batch, "items": items,
                                                           "errors": errors}, status=status)


@require_http_methods(["GET", "POST"])
@admin_required
def review_import(request, batch_id):
    batch = get_object_or_404(ImportBatch, pk=batch_id, owner=request.employee)
    if not batch.committed_at and batch.expires_at <= timezone.now():
        return render(request, "training/import_select.html", {
            "errors": ["This review expired. Select the files again."],
        }, status=410)
    if request.method == "POST":
        titles = {item["id"]: request.POST.get(f"title_{item['id']}", item["title"]) for item in batch.plan}
        try:
            try:
                revision = int(request.POST.get("revision", ""))
            except ValueError:
                raise ImportInputError("Reload this review before submitting again.") from None
            action = request.POST.get("action")
            if action not in {"save", "commit"} and not (action or "").startswith("remove:"):
                raise ImportInputError("Choose a review action.")
            batch = update_batch(batch_id=batch.pk, owner=request.employee, revision=revision, titles=titles,
                                 remove=action.partition(":")[2] if action.startswith("remove:") else None,
                                 commit=action == "commit")
        except ImportBatch.DoesNotExist:
            raise Http404("This import review is no longer available.") from None
        except ValueError as exc:
            batch.refresh_from_db()
            try:
                stale = batch.revision != int(request.POST.get("revision", ""))
            except ValueError:
                stale = True
            return _review_page(request, batch, errors=[str(exc) or "Reload this review and try again."],
                                submitted_titles=titles, status=409 if stale else 400)
        return redirect("review_import", batch_id=batch.pk)
    return _review_page(request, batch)
