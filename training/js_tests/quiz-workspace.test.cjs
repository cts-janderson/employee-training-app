const {test} = require("node:test");
const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {JSDOM} = require("jsdom");
const source = readFileSync(require("node:path").join(__dirname,"../static/training/quiz-workspace.js"),"utf8");
function setup(html='<div id="quiz-editor-workspace"><p id="save-state">Saved</p><form id="editor-form" data-unsaved="false"><input name="title"><p data-quiz-review-unsaved hidden>Unsaved</p></form></div>') {
  const dom=new JSDOM(html,{url:"http://localhost/quizzes/1/edit/",runScripts:"outside-only"});
  dom.window.eval(source);return dom;
}
for (const type of ["input","change"]) {
  test(`${type} warns that review and preview describe saved content`,()=>{
    const dom=setup(), {document}=dom.window;
    document.querySelector("input").dispatchEvent(new dom.window.Event(type,{bubbles:true}));
    assert.equal(document.querySelector("[data-quiz-review-unsaved]").hidden,false);
    assert.equal(document.getElementById("save-state").textContent,"Unsaved changes.");
    const unload=new dom.window.Event("beforeunload",{cancelable:true});
    dom.window.dispatchEvent(unload);assert.equal(unload.defaultPrevented,true);
    dom.window.close();
  });
}
test("pages without a quiz workspace remain usable",()=>{
  assert.doesNotThrow(()=>setup('<form id="editor-form"></form>').window.close());
});
test("without fetch the ordinary form still submits and navigation warning is suppressed",()=>{
  const dom=setup(), form=dom.window.document.querySelector("form");
  form.querySelector("input").dispatchEvent(new dom.window.Event("input",{bubbles:true}));
  const submit=new dom.window.Event("submit",{bubbles:true,cancelable:true});
  form.dispatchEvent(submit);assert.equal(submit.defaultPrevented,false);
  const unload=new dom.window.Event("beforeunload",{cancelable:true});
  dom.window.dispatchEvent(unload);assert.equal(unload.defaultPrevented,false);
  dom.window.close();
});

function thresholdPage(value) {
  return `<div id="quiz-editor-workspace"><p id="save-state">Saved</p><form id="editor-form" data-unsaved="false">
    <label for="passing-percent">Passing threshold</label>
    <input id="passing-percent" data-threshold-input name="passing_percent" type="number" min="1" max="100" value="${value}" aria-describedby="threshold-help">
    <span data-threshold-number-wrap hidden><input type="number" min="1" max="100" step="1" data-threshold-number></span><span data-threshold-hint hidden></span>
    <p data-quiz-review-unsaved hidden>Unsaved</p></form></div>`;
}
test("the slider displays a percentage and submits one matching threshold value",()=>{
  const dom=setup(thresholdPage("75")), {document}=dom.window;
  const slider=document.getElementById("passing-percent");
  assert.equal(slider.type,"range");
  assert.equal(slider.value,"75");
  assert.equal(slider.step,"1");
  assert.equal(document.querySelector("[data-threshold-number]").value,"75");
  slider.value="82";slider.dispatchEvent(new dom.window.Event("input",{bubbles:true}));
  assert.equal(document.querySelector("[data-threshold-number]").value,"82");
  assert.equal(slider.getAttribute("aria-valuetext"),"82%");
  assert.deepEqual(new dom.window.FormData(document.querySelector("form")).getAll("passing_percent"),["82"]);
  assert.equal(document.getElementById("save-state").textContent,"Unsaved changes.");
  dom.window.close();
});
test("an unset threshold stays blank until chosen and can be cleared again",()=>{
  const dom=setup(thresholdPage("")), {document}=dom.window, slider=document.getElementById("passing-percent");
  const posted=()=>new dom.window.FormData(document.querySelector("form")).get("passing_percent");
  assert.equal(posted(),"");assert.equal(document.querySelector("[data-threshold-number]").value,"");
  slider.value="1";slider.dispatchEvent(new dom.window.Event("input",{bubbles:true}));
  assert.equal(posted(),"1");
  slider.value="100";slider.dispatchEvent(new dom.window.Event("change",{bubbles:true}));
  assert.equal(posted(),"100");
  slider.value="0";slider.dispatchEvent(new dom.window.Event("input",{bubbles:true}));
  assert.equal(posted(),"");assert.equal(slider.getAttribute("aria-valuetext"),"Not set");
  dom.window.close();
});
test("invalid submitted thresholds are retained until the author chooses a valid value",()=>{
  for(const raw of ["bad","101","0","12.5"]) {
    const dom=setup(thresholdPage(raw)), {document}=dom.window;
    assert.equal(new dom.window.FormData(document.querySelector("form")).get("passing_percent"),raw);
    assert.equal(document.querySelector("[data-threshold-number]").placeholder,"Invalid");
    const slider=document.getElementById("passing-percent");slider.value="60";
    slider.dispatchEvent(new dom.window.Event("input",{bubbles:true}));
    assert.equal(new dom.window.FormData(document.querySelector("form")).get("passing_percent"),"60");
    dom.window.close();
  }
});

test("typing a percentage moves the slider and posts the same value",()=>{
  const dom=setup(thresholdPage("75")), {document}=dom.window;
  const number=document.querySelector("[data-threshold-number]");
  const slider=document.querySelector("[data-threshold-slider]");
  for (const value of ["1","42","100"]) {
    number.value=value;number.dispatchEvent(new dom.window.Event("input",{bubbles:true}));
    assert.equal(slider.value,value);
    assert.equal(slider.getAttribute("aria-valuetext"),`${value}%`);
    assert.deepEqual(new dom.window.FormData(document.querySelector("form")).getAll("passing_percent"),[value]);
  }
  assert.equal(document.getElementById("save-state").textContent,"Unsaved changes.");
  number.value="";number.dispatchEvent(new dom.window.Event("change",{bubbles:true}));
  assert.equal(slider.value,"0");
  assert.equal(new dom.window.FormData(document.querySelector("form")).get("passing_percent"),"");
  dom.window.close();
});

test("invalid typed percentages are flagged and do not move the slider to a misleading position",()=>{
  for(const value of ["0","101","7.5"]) {
    const dom=setup(thresholdPage("75")), {document}=dom.window;
    const number=document.querySelector("[data-threshold-number]");
    number.value=value;number.dispatchEvent(new dom.window.Event("input",{bubbles:true}));
    assert.equal(number.checkValidity(),false);
    assert.equal(number.getAttribute("aria-invalid"),"true");
    assert.equal(document.querySelector("[data-threshold-slider]").value,"75");
    const slider=document.querySelector("[data-threshold-slider]");slider.value="80";
    slider.dispatchEvent(new dom.window.Event("input",{bubbles:true}));
    assert.equal(number.value,"80");assert.equal(number.checkValidity(),true);
    dom.window.close();
  }
});
