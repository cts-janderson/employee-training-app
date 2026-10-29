const {test, afterEach} = require("node:test");
const assert = require("node:assert/strict");
const {JSDOM} = require("jsdom");
const esbuild = require("esbuild");
const Module = require("node:module");
const path = require("node:path");
const code = esbuild.buildSync({entryPoints:[path.join(__dirname,"../frontend/document-editor.js")], bundle:true,
  packages:"external", platform:"node",format:"cjs",write:false}).outputFiles[0].text;
const views=[];
afterEach(()=> { while(views.length) views.pop().destroy(); });
function setup(initial={type:"doc",content:[{type:"paragraph",content:[{type:"text",text:"Read carefully"}]}]}) {
  const dom = new JSDOM(`<p id="save-state">Saved</p><form><textarea name="body"></textarea><div data-document-editor>
    <div data-document-toolbar hidden>${["bold","italic","heading","paragraph","bullet","numbered","callout","lift","undo","redo","indent","outdent"].map(command=>`<button type="button" data-command="${command}">${command}</button>`).join("")}</div>
    <div data-document-canvas hidden></div><p data-document-status></p><input data-link-url><input data-link-text><button type="button" data-insert-link>Link</button></div></form>`,{pretendToBeVisual:true});
  global.window=dom.window;global.document=dom.window.document;global.navigator=dom.window.navigator;
  global.innerHeight=800;global.getSelection=()=>window.getSelection();
  global.requestAnimationFrame=window.requestAnimationFrame.bind(window);
  global.cancelAnimationFrame=window.cancelAnimationFrame.bind(window);
  const form=document.querySelector("form"), root=document.querySelector("[data-document-editor]");
  // Prevent auto-mount while loading exports; mount explicitly with the complete fixture.
  root.removeAttribute("data-document-editor");
  const source=form.querySelector("textarea");
  source.value=JSON.stringify(initial);
  const details=document.createElement("details");source.replaceWith(details);details.append(source);
  const mod=new Module(path.join(__dirname,"document-editor.compiled.cjs"),module);
  mod.filename=path.join(__dirname,"document-editor.compiled.cjs");mod.paths=module.paths;
  mod._compile(code,mod.filename);
  const view=mod.exports.mountEditor(form,root);views.push(view);
  const click=command=>root.querySelector(`[data-command="${command}"]`).click();
  const {TextSelection}=require("prosemirror-state");
  const select=()=>view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc,1,view.state.doc.content.size-1)));
  return {view,form,root,source,click,select,schema:mod.exports.schema};
}
test("actual formatting toolbar serializes bold, italic, headings and undo/redo",()=> {
  const {view,form,source,click,select}=setup();
  let inputEvents=0;form.addEventListener("input",()=>inputEvents++);
  select();click("bold");click("italic");
  assert.ok(inputEvents >= 2, "formatting must notify the existing unsaved-navigation guard");
  let text=JSON.parse(source.value).content[0].content[0];
  assert.deepEqual(text.marks.map(mark=>mark.type),["strong","em"]);
  click("heading");assert.equal(view.state.doc.firstChild.type.name,"heading");
  click("undo");assert.equal(view.state.doc.firstChild.type.name,"paragraph");
  click("redo");assert.equal(view.state.doc.firstChild.type.name,"heading");
  assert.equal(document.getElementById("save-state").textContent,"Unsaved changes.");
});
test("actual list and callout controls produce valid nested blocks",()=> {
  let fixture=setup();fixture.select();fixture.click("bullet");
  assert.equal(fixture.view.state.doc.firstChild.type.name,"bullet_list");fixture.view.state.doc.check();
  fixture=setup();fixture.select();fixture.click("callout");
  assert.equal(fixture.view.state.doc.firstChild.type.name,"blockquote");fixture.view.state.doc.check();
});
test("supported paste retains formatting and removes unsafe elements and links",()=> {
  const {view,source,select}=setup();select();
  view.pasteHTML('<h2>Safety</h2><p><strong>Report</strong> <em>now</em><a href="javascript:alert(1)">unsafe</a></p><script>alert(1)</script><ul><li>Inspect</li></ul>', new window.Event("paste"));
  const json=JSON.parse(source.value), serialized=JSON.stringify(json);
  assert.ok(serialized.includes('"type":"heading"'));
  assert.ok(serialized.includes('"type":"strong"'));
  assert.ok(serialized.includes('"type":"bullet_list"'));
  assert.ok(!serialized.includes("javascript:"));assert.ok(!serialized.includes("alert(1)"));
  view.state.doc.check();
});
test("reference insertion validates HTTPS and submission includes edited JSON",()=> {
  const {view,root,source,form,select}=setup();select();
  root.querySelector("[data-link-url]").value="javascript:alert(1)";
  root.querySelector("[data-insert-link]").click();assert.match(root.querySelector("[data-document-status]").textContent,/https/);
  root.querySelector("[data-link-url]").value="https://example.test/policy";
  root.querySelector("[data-link-text]").value="Policy";root.querySelector("[data-insert-link]").click();
  form.dispatchEvent(new window.Event("submit"));
  assert.match(source.value,/https:\/\/example.test\/policy/);assert.equal(view.state.doc.textContent,"Policy");
});
test("nested lists, whitespace, Unicode and legacy footer round-trip through the editor",()=> {
  const initial={type:"doc",content:[{type:"paragraph",content:[{type:"text",text:"  Δ\n text "}]},
    {type:"ordered_list",attrs:{order:3},content:[{type:"list_item",content:[{type:"paragraph",content:[{type:"text",text:"Step"}]},
      {type:"bullet_list",content:[{type:"list_item",content:[{type:"paragraph",content:[{type:"text",text:"Child"}]}]}]}]}]},
    {type:"muted",content:[{type:"text",text:"Footer\n exact "}]}]};
  const {form,source,view}=setup(initial);form.dispatchEvent(new window.Event("submit"));
  assert.deepEqual(JSON.parse(source.value),initial);view.state.doc.check();
});
