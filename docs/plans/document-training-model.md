# Document training model

Implemented locally, October 28, 2026. The user clarified that training should be a series of editable documents organized in a nested tree, with a document editor rather than a PowerPoint-style slide interface. Document versions now coexist with legacy slide versions. The implementation contract is recorded in [ADR-0008](../adr/0008-version-owned-document-content.md); editor selection is documented in the [research](../design/document-editor-research.md).

## Reference and intended experience

The [new editor screenshot](../screenshots/Screenshot%202026-10-28%20at%201.51.15%E2%80%AFPM.png) shows a document title, a broad editing canvas, a collapsed Pages control, Format and Insert controls, a formatting toolbar, and distinct saved/unpublished states. The screenshot supports a document-editing experience. It does not expose the contents of the Pages panel; nesting is the user's requested behavior.

Authors should organize the training in a left-hand outline, open one document in the main canvas, and edit its content directly. Each document is a logical file in the training. Sections organize related documents and can contain other sections. Inside a document, structured blocks support paragraphs, headings, lists, and other learning content.

```text
Workplace safety                         Training
├── Welcome                              Document
│   ├── Heading
│   └── Paragraph
├── Equipment                            Section
│   ├── Safe handling                    Document
│   │   ├── Paragraph
│   │   ├── List
│   │   │   ├── List item
│   │   │   └── List item
│   │   └── Callout
│   │       └── Paragraph
│   └── Inspection                       Document
└── Reporting an incident                Document
```

The outline hierarchy and each document's content hierarchy together provide the DOM-like structure. A section or document has an identity, a type, an ordered place among siblings, and a parent. Blocks have types, ordered children where applicable, and validated attributes. Formatting such as emphasis belongs to text content rather than becoming an acknowledgment requirement.

## Proposed content rules

A training version owns the complete outline and document content. The version root contains sections or documents. Sections contain sections or documents. Documents contain content blocks; block nesting follows allowed types, such as list → list item or callout → paragraph. Documents are edited as continuous reading material rather than fixed-height slides. The initial model does not require a separate pagination entity; the reference's Pages label can be adapted to the document outline.

The first editor should support headings, paragraphs, emphasis, links, ordered/unordered lists, and callouts. Existing supporting HTTPS links can be inserted as reference blocks. Uploaded attachments, embedded media, reusable live document references, comments, sharing controls, and AI actions need their own behavior and storage decisions; the screenshot alone does not define them.

Use stable identifiers for selection and references. A title or path is a display location, so renaming or moving an item does not create a different item within that draft. Every non-root outline item has one parent in the same version. Moves must reject cycles and keep sibling ordering valid. Reject unsupported block types, invalid nesting, excessive depth, or excessive content with a specific correction path. Establish explicit limits before implementation rather than inheriting the old 100-slide limit accidentally.

A practical storage proposal is ordered relational records for sections/documents, plus a validated structured body for each document. A document body can represent nested blocks and text marks without storing arbitrary editable HTML as the canonical content. The DOM analogy guides content structure; application content types and rendering remain controlled by the app. Choose an editor implementation after confirming round-trip support for the content schema, paste behavior, keyboard editing, and accessible output.

## Editor behavior

The outline offers Add document, Add section, Rename, Move, Duplicate, and Remove. Moving must have keyboard-accessible controls even if drag-and-drop is also added. Removing a section identifies its affected descendants and requires a concrete removal review. Duplication creates new identities for the entire copied subtree.

Selecting a document opens its title and content in a large canvas with formatting and insertion controls. Sections remain organizational items. Draft title, document title, and section title have distinct roles. The selected document and expanded outline are navigation state; they do not become learning evidence.

Preserve undo/redo within editing and the existing server revision guard. Any autosave must show Saving, Saved, or Save failed accurately and handle overlapping requests and stale revisions. Publication remains an explicit review of saved content. A saved draft is still unpublished. An initial editor can use explicit save while autosave is implemented and tested; it must never display a Saved indicator for unsaved content.

## Learner navigation and completion

Use the same published outline for learner navigation, breadcrumbs, previous/next document, and resume. Default reading order follows section/document sibling order recursively. The learner sees readable documents and a clear acknowledgment action.

The proposed evidence unit is one document. A heading, paragraph, list item, or organizational section is not independently acknowledged. For the first delivery, all training documents are required, preserving today's all-material requirement. Section completion is derived from descendant documents rather than recorded as a competing completion state. At least one nonempty document is required for publication.

An assignment completes after every required document is acknowledged and the existing attached quiz is passed when present. Keep the current single training-version quiz for this redesign; per-document quizzes would change assessment, retry, and completion rules separately. Acknowledgment continues to record an explicit action without asserting attention or comprehension.

Publication freezes the entire outline, every document body, and assessment rules together. Assigned versions remain fixed. A new revision receives independent version-specific content records; lineage may identify copied documents, but acknowledgment never transfers automatically between versions or renewal cycles.

## Changes to the existing app

| Current implementation | Proposed replacement |
| --- | --- |
| `Slide` with header/body/footer and global position | Ordered sections/documents and structured document content |
| All slide fields on one editing form | Navigable outline with one document editor |
| `Assignment.resume_slide` | Resume target identifying a document in the assigned version |
| `AssignmentSlideProgress` | Document acknowledgment per assignment and published document |
| `slides_acknowledged` and slide-gated quiz access | Required-document acknowledgment evaluation |
| By slide report and slide-count library summaries | By document report, document paths, and separately labeled document counts |
| Markdown H2 headings converted to slides | Headings represented within documents; document grouping reviewed during import |

The change touches [authoring](../../training/services.py), [the editor](../../templates/training/editor.html), [learner execution](../../training/assignments.py), [quiz submission](../../training/quizzes.py), [completion reports](../../training/completion_reports.py), and [imports](../../training/training_imports.py). Renaming labels alone would leave the existing flat content model in place.

## Delivery and migration

1. Define the outline types, document-body schema, valid nesting, limits, and migration mapping. Record the storage decision and update glossary/story definitions when settling the implementation contract.
2. Build a content module with a small interface for reading an outline, applying a revision-checked edit, and validating a publishable snapshot. Keep move/copy/remove validation inside this module so editor, imports, preview, and publication share the same rules.
3. Implement outline controls and a working document editor, including real save, reload, paste, keyboard navigation, and conflict handling. Verify that inserting and nesting content round-trips without loss.
4. Add learner rendering, document acknowledgments, resume, quiz gating, and By document reporting using the same published structure.
5. Adapt import review. Preserve source headings inside documents. Keep the existing one-file/one-training grouping unless a separate explicit grouping choice is introduced; importing several files into one training must be a visible reviewed action.
6. Migrate existing content with an explicit mapping: each slide becomes a document; header becomes its title, body becomes preserved text blocks, and footer becomes a separate muted block. Preserve every text value and the existing sequence. Retain legacy published versions, URLs, and evidence until equivalence of rendering, resume, acknowledgment, quiz gates, and reports has been demonstrated. Never silently rewrite assigned published content or discard acknowledgment records.

Acceptance includes nested move/copy/delete behavior, cycle and cross-version rejection, sibling order, stale edits, structured text round-trips, script-free safe rendering, immutable publication, migration evidence equivalence, and matching document/report/CSV populations. A complete author-to-learner test must create documents, nest sections and blocks, publish, assign, resume, acknowledge documents, pass the optional quiz, and inspect completion evidence. Editor interaction checks must exercise the actual insertion and navigation controls as well as server actions.

## Implementation and transition

- New training actions in the interface request document format. Existing direct creation clients that omit a format retain the slide contract. New revisions started from the library request document format; document revisions always remain documents.
- Existing slide drafts offer explicit revision-checked conversion. Each slide becomes one document: header → title, exact body → paragraph text, exact footer → muted block. Published slide versions, URLs, assignment resume targets, and acknowledgment records are retained without rewriting.
- The content service owns outline reads, saved edits, moves, independent subtree duplication, reviewed subtree removal, publication validation, and revision copying. Limits are defined in ADR-0008.
- The ProseMirror workspace supports headings, paragraphs, bold/italic, nested ordered/unordered lists, callouts, HTTPS links, undo/redo, and explicit save. Structured content remains available as a fallback if rich text cannot load. Pasted HTML is reduced to the supported schema; unsupported formatting is not retained. Server validation independently rejects unsafe or unsupported JSON.
- Learners use document paths, previous/next navigation, resume, explicit acknowledgments, and derived section counts. Every document is required, followed by the optional training-level quiz. Publication freezes content and assessment together.
- Library counts distinguish documents and legacy slides. Reports offer separate By document and By slide tabs, selected-version acknowledgment drilldowns, and matching exports. Assignment CSVs preserve their legacy slide columns and add content format/document columns; document cycles never populate slide evidence columns.
- New Markdown/text selections create one document per file in a new training. Markdown headings remain inside that document; other source content remains plain text. The reviewed plan records its format. Existing uncommitted slide import plans keep their original conversion until committed or expired.

This implementation uses synthetic employees. Authentication, external delivery, and real-data release gates remain deferred. Autosave, per-document quizzes, AI generation, uploads, and live shared documents remain outside this delivery.

## Verification

The focused suite covers nested moves/copies/removals, invalid parents/cycles/depth, immutable publication, stale and invalid submitted content, author-to-learner completion with a quiz, saved previews, document reports/drilldowns/CSV, import compatibility, and preservation of legacy text/evidence. Node tests exercise the actual ProseMirror toolbar, paste, reference insertion, serialization, undo/redo, and nested-content round trips. PostgreSQL-specific tests exercise competing outline saves and duplicate acknowledgments; the migration test upgrades an existing slide publication with saved resume/completion/acknowledgment evidence. See [HCI validation](../hci-validation.md#document-authoring-october-28-2026) for results and remaining manual checks.
