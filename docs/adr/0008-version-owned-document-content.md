# Version-owned document content and separate acknowledgment evidence

Document-format versions own ordered relational sections/documents and a bounded ProseMirror JSON body per document. A revision-checked content service validates nesting, moves, copies, removals, and publication; the server renders an allowlisted schema rather than trusting editable HTML. ProseMirror is bundled locally with pinned dependencies to support schema-aware editing and undo without a runtime CDN dependency.

Keep slide-format versions and their URLs/evidence intact. New document versions use separate document acknowledgments and resume targets; section progress is derived. Conversion creates a new draft, never rewrites published content or transfers progress. Existing uncommitted import plans retain their recorded conversion format.

Limits are 250 outline items, eight outline levels, 200-character titles, 100,000 characters of JSON per document, 2,000 body nodes, and twelve body levels. These are explicit document limits independent of the legacy slide limits. All documents must have a title and nonempty text before publication; sections must be named. Use explicit save; autosave and per-document assessment are deferred.
