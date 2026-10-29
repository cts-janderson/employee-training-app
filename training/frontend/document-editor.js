/*! Bundled third-party licenses: document-editor.LICENSE.txt */
import {Schema} from "prosemirror-model";
import {EditorState} from "prosemirror-state";
import {EditorView} from "prosemirror-view";
import {history, undo, redo} from "prosemirror-history";
import {keymap} from "prosemirror-keymap";
import {baseKeymap, toggleMark, setBlockType, wrapIn, lift} from "prosemirror-commands";
import {bulletList, orderedList, listItem, wrapInList, splitListItem, liftListItem, sinkListItem} from "prosemirror-schema-list";

export function safeLink(href) {
  try { const u = new URL(href); return u.protocol === "https:" && !u.username && !u.password && href.length <= 2000 && !/[\x00-\x1f]/.test(href); }
  catch { return false; }
}
export const schema = new Schema({
  nodes: {
    doc: {content: "block+"},
    paragraph: {content: "inline*", group: "block", parseDOM: [{tag:"p"}], toDOM: () => ["p",0]},
    heading: {attrs:{level:{default:1}}, content:"inline*", group:"block", defining:true,
      parseDOM:[1,2,3].map(level => ({tag:`h${level+1}`, attrs:{level}})), toDOM:node => [`h${node.attrs.level+1}`,0]},
    text: {group:"inline"},
    hard_break: {inline:true, group:"inline", selectable:false, parseDOM:[{tag:"br"}], toDOM:() => ["br"]},
    bullet_list: {...bulletList, content:"list_item+", group:"block"},
    ordered_list: {...orderedList, content:"list_item+", group:"block"},
    list_item: {...listItem, content:"paragraph block*"},
    blockquote: {content:"block+", group:"block", defining:true, parseDOM:[{tag:"blockquote"}], toDOM:() => ["blockquote",0]},
    muted: {content:"inline*", group:"block", parseDOM:[{tag:"p.muted", priority:60}], toDOM:() => ["p",{class:"muted"},0]},
  },
  marks: {
    strong: {parseDOM:[{tag:"strong"},{tag:"b"}], toDOM:() => ["strong",0]},
    em: {parseDOM:[{tag:"em"},{tag:"i"}], toDOM:() => ["em",0]},
    link: {attrs:{href:{}}, inclusive:false, parseDOM:[{tag:"a[href]", getAttrs:el => safeLink(el.getAttribute("href")) ? {href:el.getAttribute("href")} : false}],
      toDOM:mark => ["a",{href:mark.attrs.href, rel:"noopener noreferrer"},0]},
  },
});

export function mountEditor(form, root) {
  const source = form.querySelector('[name="body"]');
  const canvas = root.querySelector("[data-document-canvas]");
  const toolbar = root.querySelector("[data-document-toolbar]");
  const status = root.querySelector("[data-document-status]");
  if (!source || !canvas || !toolbar) return null;
  const initial = schema.nodeFromJSON(JSON.parse(source.value));
  initial.check();
  let view;
  function dirty() {
    form.dataset.unsaved = "true";
    source.dispatchEvent(new window.Event("input", {bubbles:true}));
    const state = document.getElementById("save-state");
    if (state) state.textContent = "Unsaved changes.";
  }
  view = new EditorView(canvas, {
    state: EditorState.create({doc:initial, plugins:[history(), keymap({
      "Mod-z":undo, "Mod-y":redo, "Shift-Mod-z":redo,
      "Mod-b":toggleMark(schema.marks.strong), "Mod-i":toggleMark(schema.marks.em),
      "Enter":splitListItem(schema.nodes.list_item), "Mod-]":sinkListItem(schema.nodes.list_item),
      "Mod-[":liftListItem(schema.nodes.list_item),
      "Shift-Enter":(state, dispatch) => { if(dispatch) dispatch(state.tr.replaceSelectionWith(schema.nodes.hard_break.create()).scrollIntoView()); return true; },
    }), keymap(baseKeymap)]}),
    attributes:{role:"textbox", "aria-multiline":"true", "aria-label":"Document content"},
    dispatchTransaction(transaction) {
      view.updateState(view.state.apply(transaction));
      if (transaction.docChanged) { source.value = JSON.stringify(view.state.doc.toJSON()); dirty(); }
      updateControls();
    },
  });
  const commands = {
    bold:toggleMark(schema.marks.strong), italic:toggleMark(schema.marks.em),
    paragraph:setBlockType(schema.nodes.paragraph), heading:setBlockType(schema.nodes.heading,{level:1}),
    subheading:setBlockType(schema.nodes.heading,{level:2}),
    bullet:wrapInList(schema.nodes.bullet_list), numbered:wrapInList(schema.nodes.ordered_list),
    callout:wrapIn(schema.nodes.blockquote), lift,
    indent:sinkListItem(schema.nodes.list_item), outdent:liftListItem(schema.nodes.list_item),
    undo, redo,
  };
  function updateControls() {
    for (const button of toolbar.querySelectorAll("[data-command]")) {
      const command = commands[button.dataset.command];
      if (command) button.disabled = !command(view.state);
    }
  }
  toolbar.addEventListener("mousedown", event => { if(event.target.closest("button")) event.preventDefault(); });
  toolbar.addEventListener("click", event => {
    const button = event.target.closest("button[data-command]");
    if (!button) return;
    commands[button.dataset.command]?.(view.state, view.dispatch, view);
    view.focus();
  });
  root.querySelector("[data-insert-link]").addEventListener("click", () => {
    const href = root.querySelector("[data-link-url]").value.trim();
    if (!safeLink(href)) { status.textContent = "Enter a valid https:// link."; return; }
    const label = root.querySelector("[data-link-text]").value.trim() || href;
    view.dispatch(view.state.tr.replaceSelectionWith(schema.text(label,[schema.marks.link.create({href})]), false).scrollIntoView());
    status.textContent = "Link inserted.";
    view.focus();
  });
  form.addEventListener("submit", () => { source.value = JSON.stringify(view.state.doc.toJSON()); });
  source.closest("details").hidden = true;
  toolbar.hidden = false;
  canvas.hidden = false;
  updateControls();
  return view;
}

for (const root of document.querySelectorAll("[data-document-editor]")) {
  try { mountEditor(root.closest("form"), root); }
  catch { root.querySelector("[data-document-status]").textContent = "The editor could not load. Copy the recovery text below before refreshing."; }
}
