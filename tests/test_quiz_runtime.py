import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "app.js"
NODE = shutil.which("node")


NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const appSource = fs.readFileSync(process.argv[1], "utf8");
const start = appSource.indexOf("function renderQuiz(");
const end = appSource.indexOf("\nfunction updateExamReadiness", start);
if (start < 0 || end < 0) throw new Error("quiz runtime block not found");
const quizRuntime = appSource.slice(start, end);

const scenario = `
const listeners = [];
const readiness = [];
let feedbackFocuses = 0;
const quizContent = {
  html: "",
  set innerHTML(value) { this.html = String(value); },
  get innerHTML() { return this.html; },
  addEventListener(type, listener) { if (type === "click") listeners.push(listener); },
  contains(node) { return Boolean(node?.insideQuiz); },
};
const quizStatus = {textContent: ""};
const elements = {quizContent, quizStatus};
function $(id) {
  if (id.startsWith("quiz-feedback-")) return {focus() { feedbackFocuses += 1; }};
  return elements[id] || null;
}
let currentLanguage = "tr";
const messages = {
  tr: {score:"Skor", correct:"Doğru", incorrect:"Yanlış", noContent:"İçerik yok"},
  en: {score:"Score", correct:"Correct", incorrect:"Incorrect", noContent:"No content"},
};
function t(key) { return messages[currentLanguage][key] || key; }
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"})[char]);
}
let activeQuizItems = [], quizAnswers = new Map(), quizRendered = false;
let quizScore = 0, quizAnswered = 0;
function updateExamReadiness(total) { readiness.push({total, score:quizScore, answered:quizAnswered}); }

${quizRuntime}

function target(question, option, insideQuiz = true) {
  const shell = {insideQuiz, dataset:{question:String(question)}};
  return {
    insideQuiz,
    disabled:false,
    dataset:{option:String(option)},
    closest(selector) {
      if (selector === ".quiz-option") return this;
      if (selector === ".quiz-item") return shell;
      return null;
    },
  };
}

const questions = [
  {question:"2 < 3?", options:["Hayır", "Evet"], answer_index:1, explanation:"İki, üçten küçüktür."},
  {question:"Gökyüzü?", options:["Mavi", "Yeşil"], answer_index:0, explanation:"Gündüz mavidir."},
];
renderQuiz(questions);
const initial = {html:quizContent.innerHTML, status:quizStatus.textContent, listeners:listeners.length};

listeners[0]({target:target(0, 0)});
const wrong = {html:quizContent.innerHTML, status:quizStatus.textContent, readiness:[...readiness], feedbackFocuses};

const rendersBeforeDuplicate = readiness.length;
listeners[0]({target:target(0, 0)});
const duplicate = {html:quizContent.innerHTML, status:quizStatus.textContent, renders:readiness.length - rendersBeforeDuplicate};

listeners[0]({target:target(1, 0, false)});
const outside = {status:quizStatus.textContent, answered:quizAnswered};

currentLanguage = "en";
renderQuiz(undefined, {reset:false});
const translated = {html:quizContent.innerHTML, status:quizStatus.textContent, answered:quizAnswered, score:quizScore, listeners:listeners.length};

renderQuiz([{question:"New", options:["A", "B"], answer_index:0, explanation:"A wins."}]);
const restarted = {html:quizContent.innerHTML, status:quizStatus.textContent, answered:quizAnswered, score:quizScore, listeners:listeners.length};
listeners[0]({target:target(0, 0)});
const correct = {html:quizContent.innerHTML, status:quizStatus.textContent, answered:quizAnswered, score:quizScore};

console.log(JSON.stringify({initial, wrong, duplicate, outside, translated, restarted, correct}));
`;

vm.runInNewContext(scenario, {console});
"""


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for quiz runtime tests")


def run_quiz_scenario() -> dict:
    completed = subprocess.run(
        [NODE, "-e", NODE_HARNESS, str(APP)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_quiz_click_is_scoped_idempotent_and_accessible() -> None:
    result = run_quiz_scenario()

    assert result["initial"]["listeners"] == 1
    assert result["initial"]["status"] == "Skor: 0/2"
    assert 'type="button"' in result["initial"]["html"]
    assert 'role="group"' in result["initial"]["html"]

    wrong = result["wrong"]
    assert wrong["status"] == "Skor: 0/2 · 1/2"
    assert 'class="quiz-option selected wrong"' in wrong["html"]
    assert 'class="quiz-option correct"' in wrong["html"]
    assert 'aria-pressed="true"' in wrong["html"]
    assert 'aria-describedby="quiz-feedback-0" disabled' in wrong["html"]
    assert '<span class="quiz-option-result">— Yanlış</span>' in wrong["html"]
    assert '<span class="quiz-option-result">— Doğru</span>' in wrong["html"]
    assert 'role="status" tabindex="-1"><strong>Yanlış</strong>' in wrong["html"]
    assert wrong["feedbackFocuses"] == 1

    assert result["duplicate"]["renders"] == 0
    assert result["duplicate"]["status"] == wrong["status"]
    assert result["outside"] == {"status": wrong["status"], "answered": 1}


def test_quiz_language_rerender_preserves_answer_and_restart_resets_it() -> None:
    result = run_quiz_scenario()

    translated = result["translated"]
    assert translated["status"] == "Score: 0/2 · 1/2"
    assert translated["answered"] == 1
    assert translated["score"] == 0
    assert translated["listeners"] == 1
    assert '<span class="quiz-option-result">— Incorrect</span>' in translated["html"]
    assert '<span class="quiz-option-result">— Correct</span>' in translated["html"]

    restarted = result["restarted"]
    assert restarted["status"] == "Score: 0/1"
    assert restarted["answered"] == 0
    assert restarted["score"] == 0
    assert restarted["listeners"] == 1
    assert " answered" not in restarted["html"]
    assert " disabled" not in restarted["html"]

    correct = result["correct"]
    assert correct["status"] == "Score: 1/1 · 1/1"
    assert correct["answered"] == 1
    assert correct["score"] == 1
    assert '<strong>Correct</strong>' in correct["html"]


def test_language_refresh_uses_preserving_quiz_render() -> None:
    source = APP.read_text(encoding="utf-8")
    apply_language = source[source.index("function applyLanguage()") : source.index("\n\nuiLanguage.replaceChildren")]
    quiz_runtime = source[source.index("function renderQuiz(") : source.index("\nfunction updateExamReadiness")]

    assert 'if (quizRendered) renderQuiz(undefined, {reset:false});' in apply_language
    assert 'quizContentElement.addEventListener("click"' in quiz_runtime
    assert 'document.querySelectorAll(".quiz-option")' not in quiz_runtime
