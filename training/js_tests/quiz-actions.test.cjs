const {test} = require("node:test");
const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const {JSDOM} = require("jsdom");
const source = readFileSync(join(__dirname,"../static/training/quiz-workspace.js"),"utf8");

function page(revision=0, {extra="", error=""}={}) {
  return `<!doctype html><div id="quiz-editor-workspace" data-quiz-editor>
    <div id="quiz-request-status" role="status"></div>${error}
    <p id="save-state">Saved revision ${revision}.</p>
    <form method="post" id="editor-form" data-unsaved="false">
      <input name="csrfmiddlewaretoken" value="test-csrf"><input name="revision" value="${revision}">
      <input name="title" value="Safety"><input name="passing_percent" value="75">
      <section class="quiz-editor-question" data-question-id="q1" id="question-1">
        <h2 id="question-heading-q1" tabindex="-1">Question 1</h2>
        <textarea id="prompt-q1" name="question_prompt">Saved prompt</textarea>
        <button name="action" value="add_option:q1">Add option</button>
        <button name="action" value="review_remove_question:q1">Remove question</button>
        ${extra}
      </section>
      <button name="action" value="save">Save quiz</button>
      <p data-quiz-review-unsaved hidden>Unsaved edits</p>
    </form></div>`;
}
function setup(fetcher=async()=>({status:200,ok:true,redirected:true,url:"http://localhost/quizzes/q/edit/#option-new",headers:{get:()=>"text/html"},text:async()=>page(1,{extra:'<input id="option-new" name="option_text_q1" value="">'})})) {
  const dom = new JSDOM(page(), {url:"http://localhost/quizzes/q/edit/",runScripts:"outside-only",pretendToBeVisual:true});
  const {window}=dom;
  const calls=[];
  window.fetch=async(...args)=>{calls.push(args);return fetcher(...args);};
  window.scrollTo=(...args)=>{window.lastScroll=args;};
  Object.defineProperty(window,"scrollY",{value:420,writable:true});
  window.eval(source);
  const submit=(value)=>{
    const form=window.document.getElementById("editor-form");
    const button=[...form.querySelectorAll("button")].find(b=>b.value===value);
    const event=new window.SubmitEvent("submit",{bubbles:true,cancelable:true,submitter:button});
    form.dispatchEvent(event);return event;
  };
  const settle=async()=>{for(let i=0;i<8;i++) await Promise.resolve(); await new Promise(resolve=>setTimeout(resolve,0));};
  return {dom,window,document:window.document,calls,submit,settle};
}

test("Add option is handled without navigation or losing the current scroll position",async()=>{
  const f=setup();
  const event=f.submit("add_option:q1");
  assert.equal(event.defaultPrevented,true,"the browser must not reload the page");
  await f.settle();
  assert.equal(f.calls.length,1);
  assert.equal(f.calls[0][1].body.get("action"),"add_option:q1");
  assert.equal(f.calls[0][1].body.get("csrfmiddlewaretoken"),"test-csrf");
  assert.equal(f.document.querySelector('[name="revision"]').value,"1");
  assert.ok(f.document.getElementById("option-new"));
  assert.equal(f.window.location.hash,"");
  assert.equal(f.window.lastScroll[1],420);
  f.dom.window.close();
});

function response(html, status=200) {
  return {status,headers:{get:()=>"text/html; charset=utf-8"},url:"http://localhost/quizzes/q/edit/",text:async()=>html};
}
function unloadPrevented(f) {
  const event=new f.window.Event("beforeunload",{cancelable:true});
  f.window.dispatchEvent(event);return event.defaultPrevented;
}

test("handlers survive replacement and submit the newly returned revision",async()=>{
  let revision=0;
  const f=setup(async()=>response(page(++revision)));
  f.submit("save");await f.settle();
  f.document.querySelector("textarea").value="Edited again";
  f.document.querySelector("textarea").dispatchEvent(new f.window.Event("input",{bubbles:true}));
  assert.equal(unloadPrevented(f),true);
  f.submit("save");await f.settle();
  assert.equal(f.calls[1][1].body.get("revision"),"1");
  assert.equal(f.calls[1][1].body.get("question_prompt"),"Edited again");
  assert.equal(f.document.querySelector('[name="revision"]').value,"2");
  assert.equal(unloadPrevented(f),false);
  f.dom.window.close();
});

test("a failed request preserves edits, re-enables controls, and keeps the unsaved warning",async()=>{
  const f=setup(async()=>{throw new Error("Disconnected");});
  f.document.querySelector("textarea").value="Do not lose this";
  f.document.querySelector("textarea").dispatchEvent(new f.window.Event("input",{bubbles:true}));
  f.submit("save");await f.settle();
  assert.equal(f.calls.length,1,"uncertain writes must never be retried automatically");
  assert.equal(f.document.querySelector("textarea").value,"Do not lose this");
  assert.equal(f.document.querySelector("textarea").disabled,false);
  assert.equal(f.document.querySelector('[name="revision"]').value,"0");
  assert.match(f.document.getElementById("quiz-request-status").textContent,/Could not confirm/);
  assert.equal(unloadPrevented(f),true);
  assert.equal(f.window.lastScroll[1],420);
  f.dom.window.close();
});

test("a request in flight blocks duplicate clicks and later edits",async()=>{
  let complete;
  const f=setup(()=>new Promise(resolve=>{complete=resolve;}));
  f.submit("add_option:q1");
  assert.equal(f.document.querySelector("textarea").disabled,true);
  assert.equal(f.document.getElementById("quiz-editor-workspace").getAttribute("aria-busy"),"true");
  f.submit("add_option:q1");
  assert.equal(f.calls.length,1);
  assert.equal(unloadPrevented(f),true);
  complete(response(page(1)));await f.settle();
  assert.equal(f.document.querySelector("textarea").disabled,false);
  assert.equal(f.document.getElementById("quiz-editor-workspace").hasAttribute("aria-busy"),false);
  f.dom.window.close();
});

for (const [status,id] of [[400,"quiz-editor-errors"],[409,"quiz-conflict"]]) {
  test(`${status} keeps server validation/conflict feedback beside the affected question`,async()=>{
    const html=page(0,{error:`<section id="${id}" role="alert" tabindex="-1">Not saved</section>`}).replace('data-unsaved="false"','data-unsaved="true"');
    const f=setup(async()=>response(html,status));
    f.submit("add_option:q1");await f.settle();
    assert.ok(f.document.getElementById(id).closest('[data-question-id="q1"]'));
    assert.equal(f.document.activeElement.id,id);
    assert.equal(f.document.querySelector('[name="revision"]').value,"0");
    assert.equal(unloadPrevented(f),true);
    assert.equal(f.window.lastScroll[1],420);
    f.dom.window.close();
  });
}

test("removal review and cancel stay in place without claiming a save",async()=>{
  let action=0;
  const review='<section id="remove-review" tabindex="-1"><button name="action" value="cancel_remove">Keep it</button></section>';
  const f=setup(async()=>response(page(0,{extra:++action===1 ? review : ""}).replace('data-unsaved="false"','data-unsaved="true"')));
  f.submit("review_remove_question:q1");await f.settle();
  assert.equal(f.document.querySelector('[data-question-id="q1"]').nextElementSibling.id,"remove-review");
  assert.equal(f.document.activeElement.id,"remove-review");
  assert.equal(unloadPrevented(f),true);
  f.submit("cancel_remove");await f.settle();
  assert.equal(f.document.getElementById("remove-review"),null);
  assert.match(f.document.getElementById("quiz-request-status").textContent,/still unsaved/);
  assert.equal(f.window.lastScroll[1],420);
  f.dom.window.close();
});

test("unexpected or error pages never replace the author's fields",async()=>{
  for (const reply of [response("<h1>Sign in</h1>"),response(page(2),500),{status:200,headers:{get:()=>"application/json"}}]) {
    const f=setup(async()=>reply);
    f.document.querySelector("textarea").value="Keep my answer";
    f.submit("save");await f.settle();
    assert.equal(f.document.querySelector("textarea").value,"Keep my answer");
    assert.equal(f.document.querySelector('[name="revision"]').value,"0");
    assert.match(f.document.getElementById("quiz-request-status").textContent,/Could not confirm/);
    f.dom.window.close();
  }
});

test("ordering warnings work after a structural update",async()=>{
  const extra='<fieldset data-ordering-editor><input type="number" value="1"><input type="number" value="2"><p data-rank-warning></p></fieldset>';
  const f=setup(async()=>response(page(1,{extra})));
  f.submit("save");await f.settle();
  const ranks=f.document.querySelectorAll('input[type="number"]');
  ranks[1].value="1";ranks[1].dispatchEvent(new f.window.Event("input",{bubbles:true}));
  assert.match(f.document.querySelector("[data-rank-warning]").textContent,/only once/);
  assert.equal(unloadPrevented(f),true);
  f.dom.window.close();
});

test("reordering preserves the clicked question's viewport offset without changing the URL",async()=>{
  const f=setup(async()=>response(page(1)));
  f.document.querySelector('[data-question-id="q1"]').getBoundingClientRect=()=>({top:30,bottom:200});
  const original=f.window.HTMLElement.prototype.getBoundingClientRect;
  f.window.HTMLElement.prototype.getBoundingClientRect=function(){
    return this.dataset.questionId ? {top:180,bottom:350} : original.call(this);
  };
  f.submit("add_option:q1");await f.settle();
  assert.equal(f.window.lastScroll[1],570);
  assert.equal(f.window.location.href,"http://localhost/quizzes/q/edit/");
  f.dom.window.close();
});

test("the percentage slider is initialized after in-place updates and saves its current value",async()=>{
  const sliderHTML='<div><input id="passing-percent" data-threshold-input name="passing_percent" type="number" value="75"><span data-threshold-number-wrap hidden><input data-threshold-number type="number" min="1" max="100" step="1"></span></div>';
  const f=setup(async()=>response(page(1).replace('<input name="passing_percent" value="75">',sliderHTML)));
  f.submit("save");await f.settle();
  const slider=f.document.getElementById("passing-percent");
  assert.equal(slider.type,"range");
  assert.equal(f.document.querySelector("[data-threshold-number]").value,"75");
  slider.value="88";slider.dispatchEvent(new f.window.Event("input",{bubbles:true}));
  assert.equal(f.document.querySelector("[data-threshold-number]").value,"88");
  f.submit("save");await f.settle();
  assert.deepEqual(f.calls[1][1].body.getAll("passing_percent"),["88"]);
  f.dom.window.close();
});

test("typed percentages remain synchronized after an update and invalid amounts do not submit",async()=>{
  const controls='<div><input id="passing-percent" data-threshold-input name="passing_percent" type="number" value="75"><span data-threshold-number-wrap hidden><input data-threshold-number type="number" min="1" max="100" step="1"></span></div>';
  const f=setup(async()=>response(page(1).replace('<input name="passing_percent" value="75">',controls)));
  f.submit("save");await f.settle();
  const number=f.document.querySelector("[data-threshold-number]");
  number.value="101";number.dispatchEvent(new f.window.Event("input",{bubbles:true}));
  f.submit("save");await f.settle();
  assert.equal(f.calls.length,1);
  number.value="91";number.dispatchEvent(new f.window.Event("input",{bubbles:true}));
  assert.equal(f.document.querySelector("[data-threshold-slider]").value,"91");
  f.submit("save");await f.settle();
  assert.deepEqual(f.calls[1][1].body.getAll("passing_percent"),["91"]);
  f.dom.window.close();
});
