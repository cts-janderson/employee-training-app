# Document editor research

Investigated October 28, 2026 for the [document training model](../plans/document-training-model.md). This is a source-based selection recommendation; interaction feasibility must be demonstrated by the implementation tests below.

## Recommendation

Use ProseMirror with an application-owned schema and locally bundled JavaScript. The deciding factor is its schema-defined tree of blocks and inline marks: this matches headings, paragraphs, nested lists, callouts, references, and a preserved muted legacy footer directly. Its schema specifies allowed nodes and nesting, including custom types. This fit is an engineering judgment based on the [official schema guide source](https://raw.githubusercontent.com/ProseMirror/website/master/markdown/guide/schema.md) and [document-model guide source](https://raw.githubusercontent.com/ProseMirror/website/master/markdown/guide/doc.md).

Quill remains a viable choice for conventional formatted text. Its Delta format represents content and edits as JSON operations; line formatting belongs to newline characters, and its own format imposes some mutually exclusive line formats. It would require an additional mapping into the proposed nested block contract. That mapping is avoidable with ProseMirror. See [Quill Delta](https://quilljs.com/docs/delta).

## Comparison

| Concern | ProseMirror | Quill |
| --- | --- | --- |
| Canonical content | Recursive document nodes with marks; custom schema controls nesting. | Delta operations encode text, attributes, line formats, and embeds. |
| Callouts and legacy footer | Application-defined block types within the schema. | Custom Blots/Attributors through Parchment, plus mapping into the application contract. |
| Editing UI | Core handles typing, selection, paste, and dragging; toolbar and complete keybindings must be supplied. | Snow/Bubble themes and configurable modules offer more ready-made UI. |
| Undo/redo | Explicit history plugin. | History module with undo/redo APIs. |
| Application work | Build focused toolbar and keyboard bindings; validate JSON again on the server. | Configure formats/modules; build custom formats and conversion if retaining the proposed nested schema. |

The ProseMirror UI responsibilities and paste-to-schema behavior are described in the [official view guide source](https://raw.githubusercontent.com/ProseMirror/website/master/markdown/guide/view.md). Quill themes, modules, history, and custom formats are documented in [Quill customization](https://quilljs.com/docs/customization). ProseMirror's [history module](https://raw.githubusercontent.com/ProseMirror/prosemirror-history/master/README.md) supplies undo/redo.

## Integration contract

- Keep the training outline separate from each document body. Editor block positions are not outline-item identities or acknowledgment units.
- Store structured JSON rather than editable HTML. Restrict the initial schema to the requested content types and attributes.
- Parse pasted HTML through the editor schema. ProseMirror's [DOM parser source](https://raw.githubusercontent.com/ProseMirror/prosemirror-model/master/src/from_dom.ts) documents schema-based parsing and whitespace controls. Unsupported content needs a defined visible behavior; do not promise arbitrary rich-document fidelity.
- Treat browser validation as convenience. Independently validate nesting, attributes, links, depth, and size on the server. Render escaped text and allowlisted application markup; never trust client-generated HTML.
- Use explicit save with the existing version revision guard. Serialization must update the submitted body before every save or structural submission; edits must remain available after validation or conflict responses.
- Follow the [official list source](https://raw.githubusercontent.com/ProseMirror/prosemirror-schema-list/master/src/schema-list.ts): the standard list commands expect list items shaped like `paragraph block*`. Bind list split, indent, and outdent commands explicitly.

## Reproducible dependencies

These initial candidate versions were read from first-party source manifests. The installed lockfile supersedes these candidates: `prosemirror-view` was updated to 1.42.8 to address its paste-handling advisory, and `prosemirror-model` to 1.25.12 to match the patched view dependency and avoid duplicate model instances. Installation reported zero known vulnerabilities after these updates. They are suitable candidate pins, not a claim about the latest registry releases. Resolve them together through npm, commit the lockfile, and verify the resulting bundle. Keep license notices with distributed assets. Component source repositories now point to the maintainer's `code.haverbeke.berlin` hosting; GitHub archive banners alone do not mean the project was discontinued.

| Package | Inspected version and first-party manifest |
| --- | --- |
| `prosemirror-model` | [1.25.4](https://raw.githubusercontent.com/ProseMirror/prosemirror-model/master/package.json) |
| `prosemirror-state` | [1.4.4](https://raw.githubusercontent.com/ProseMirror/prosemirror-state/master/package.json) |
| `prosemirror-view` | [1.41.7](https://raw.githubusercontent.com/ProseMirror/prosemirror-view/master/package.json) |
| `prosemirror-history` | [1.5.0](https://raw.githubusercontent.com/ProseMirror/prosemirror-history/master/package.json) |
| `prosemirror-keymap` | [1.2.3](https://raw.githubusercontent.com/ProseMirror/prosemirror-keymap/master/package.json) |
| `prosemirror-commands` | [1.7.1](https://raw.githubusercontent.com/ProseMirror/prosemirror-commands/master/package.json) |
| `prosemirror-schema-list` | [1.5.1](https://raw.githubusercontent.com/ProseMirror/prosemirror-schema-list/master/package.json) |

Bundle the editor and application code using esbuild during development and serve the resulting static asset locally. This bundling approach is an application recommendation; no CDN is required at runtime. Choose and pin the build-tool version when generating the lockfile.

## Feasibility and acceptance checks

- Round-trip every supported block/mark through JSON, save, reload, server validation, and learner rendering. Preserve newlines, Unicode, leading/trailing whitespace, and the muted legacy footer according to the agreed schema.
- Exercise actual toolbar commands, typing, list nesting, callout insertion, selection, undo, and redo. Model-only tests do not establish editor usability.
- Paste supported headings/lists/emphasis, plain multiline text, unsupported elements, and script-bearing/link-bearing HTML. Verify both supported-content preservation and safe rejection or conversion.
- Test keyboard reachability, labeled controls, visible focus, undo shortcuts, list navigation, narrow layout, and reading order. Automated DOM tests complement direct browser and assistive-technology review.
- Submit invalid and stale edits, reload after a successful explicit save, and attempt publication from saved content. A failed save must not display Saved.
- Verify server rejection of unsupported nodes/marks, unsafe links, excessive depth/size, malformed JSON, and cross-version outline operations. Render no arbitrary HTML from submitted content.

Official `prosemirror.net` guide/reference requests returned HTTP 403 in this research session. The linked guide files are the project's own website sources, not third-party explanations. Registry metadata was unavailable through the browsing tool, so the table deliberately distinguishes inspected source versions from latest published versions.
