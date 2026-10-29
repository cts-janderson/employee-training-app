"""Dispatch version-specific reading and evidence without rewriting legacy assignments."""
def units(version):
    if version.content_format == "documents":
        from .documents import documents
        return documents(version)
    return list(version.slides.all())


def acknowledged_ids(assignment):
    if assignment.version.content_format == "documents":
        return {entry.document_id for entry in assignment.document_progress.all()}
    return {entry.slide_id for entry in assignment.slide_progress.all()}


def resume_id(assignment):
    return assignment.resume_document_id if assignment.version.content_format == "documents" else assignment.resume_slide_id


def complete(assignment):
    required = {item.pk for item in units(assignment.version)}
    return (bool(required) or assignment.version.content_format == "slides") and required <= acknowledged_ids(assignment)
