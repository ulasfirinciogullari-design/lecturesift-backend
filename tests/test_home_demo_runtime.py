"""Offline home sample behavior and production translation coverage."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


NODE_HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = process.argv[1];
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const html = read('frontend/index.html');
const i18nSource = read('frontend/i18n.js');
const demoSource = read('frontend/home-demo.js');
const builderSource = read('scripts/build_localized_site.mjs');
const languages = ['tr', 'en', 'de', 'fr', 'es', 'it', 'pt', 'ru', 'ar', 'zh', 'ja', 'ko', 'hi'];
const keys = ['tabsLabel', 'sample', 'question', 'a', 'b', 'c', 'correct', 'incorrect', 'retry'];

// Execute the production builder's catalog parser and translator without
// copying a site or writing build artifacts for this focused sample test.
const translationStart = builderSource.indexOf('const keyCatalog = {};');
const translationEnd = builderSource.indexOf('\nfunction deferNonCriticalScripts', translationStart);
assert.ok(translationStart >= 0 && translationEnd > translationStart);
const translator = vm.runInNewContext(
  builderSource.slice(translationStart, translationEnd) + ';({keyCatalog, translateDocument})',
  {LANGUAGES:languages, dynamicCopySource:i18nSource, referralCopySource:read('frontend/referral-i18n.js'), catalog:{}},
);
for (const key of keys) {
  const values = translator.keyCatalog[`homeDemo.${key}`];
  assert.equal(values?.length, languages.length, key);
  assert.ok(values.every(value => typeof value === 'string' && value.trim()), key);
}

class Element {
  constructor(attributes = {}, text = '', parent = null) {
    this.attributes = {...attributes};
    this.textContent = text;
    this.parentElement = parent;
    this.dataset = Object.fromEntries(Object.entries(attributes).filter(([key]) => key.startsWith('data-')).map(([key, value]) => [key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()), value]));
    this.hidden = 'hidden' in attributes;
    this.disabled = false;
    this.tabIndex = Number(attributes.tabindex || 0);
    this.classes = new Set((attributes.class || '').split(' ').filter(Boolean));
    this.classList = {add:(...names) => names.forEach(name => this.classes.add(name)), remove:(...names) => names.forEach(name => this.classes.delete(name))};
  }
  matches(selector) {
    if (selector.startsWith('#')) return this.attributes.id === selector.slice(1);
    const match = selector.match(/^\[([^=\]]+)(?:="([^"]+)")?\]$/);
    return Boolean(match && match[1] in this.attributes && (match[2] === undefined || this.attributes[match[1]] === match[2]));
  }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  focus() { this.focusCount = (this.focusCount || 0) + 1; }
}

function sampleDocument(markup) {
  const listeners = {};
  const host = new Element();
  const nodes = [];
  for (const match of markup.matchAll(/<(button|small|h3|div|p)\b([^>]*)>([^<]*)/g)) {
    const attributes = Object.fromEntries([...match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)].map(value => [value[1], value[2] ?? '']));
    if ('data-demo-copy' in attributes || attributes.id?.startsWith('demo')) nodes.push(new Element(attributes, match[3], host));
  }
  host.querySelectorAll = selector => nodes.filter(node => node.matches(selector));
  host.querySelector = selector => host.querySelectorAll(selector)[0] || null;
  host.contains = node => { for (; node; node = node.parentElement) if (node === host) return true; return false; };
  host.addEventListener = (type, callback) => { assert.equal(listeners[type], undefined); listeners[type] = callback; };
  const document = {
    documentElement:{lang:'tr', dir:'ltr'}, body:{}, head:{append() {}}, title:'LectureSift',
    querySelector: selector => selector === '.home-page [data-study-demo]' ? host : null,
    querySelectorAll: selector => host.querySelectorAll(selector),
    getElementById: id => nodes.find(node => node.attributes.id === id) || null,
    createElement: () => new Element(), createTreeWalker: () => ({nextNode:() => false}),
  };
  return {document, host, listeners, nodes};
}

const results = [];
for (const [index, language] of languages.entries()) {
  const translated = translator.translateDocument(html, language);
  assert.equal(translated.match(/role="tablist" aria-label="([^"]+)"/)?.[1], translator.keyCatalog['homeDemo.tabsLabel'][index]);
  const {document, host, listeners} = sampleDocument(translated);
  const snapshot = () => ({
    feedback:document.getElementById('demoFeedback').textContent,
    resetHidden:document.getElementById('demoReset').hidden,
    answers:host.querySelectorAll('[data-demo-answer]').map(node => ({disabled:node.disabled, classes:[...node.classes].sort(), pressed:node.getAttribute('aria-pressed')})),
  });
  for (const node of host.querySelectorAll('[data-demo-copy]')) {
    assert.equal(node.dataset.i18n, `homeDemo.${node.dataset.demoCopy}`);
    assert.equal(node.textContent, translator.keyCatalog[node.dataset.i18n][index], `${language} prerender ${node.dataset.i18n}`);
  }
  const pathname = language === 'tr' ? '/' : `/${language}/`;
  const forbidden = name => () => { throw Error(`Sample must not use ${name}`); };
  const context = vm.createContext({
    document, window:{}, NodeFilter:{SHOW_TEXT:4}, URL,
    location:{pathname, href:`https://lecturesift.com${pathname}`, origin:'https://lecturesift.com', hostname:'lecturesift.com'},
    localStorage:new Proxy({}, {get:forbidden('localStorage')}),
    sessionStorage:new Proxy({}, {get:forbidden('sessionStorage')}),
    fetch:forbidden('network'), navigator:{language:'en'},
  });
  vm.runInContext(i18nSource, context);
  vm.runInContext(demoSource, context);
  assert.equal(context.window.LectureSiftI18n.language, language);
  assert.equal(document.documentElement.dir, language === 'ar' ? 'rtl' : 'ltr');
  for (const node of host.querySelectorAll('[data-demo-copy]')) assert.equal(node.textContent, translator.keyCatalog[node.dataset.i18n][index]);

  const tabs = host.querySelectorAll('[role="tab"]');
  const options = host.querySelectorAll('[data-demo-answer]');
  const click = target => listeners.click({target});
  const keydown = (target, key) => { let prevented = false; listeners.keydown({target, key, preventDefault() { prevented = true; }}); return prevented; };
  const nested = parent => new Element({}, '', parent);
  assert.equal(tabs.length, 2);
  assert.equal(options.length, 3);
  assert.deepEqual(snapshot().answers.map(option => option.disabled), [false, false, false]);

  click(nested(tabs[1]));
  assert.equal(tabs[1].getAttribute('aria-selected'), 'true');
  assert.equal(tabs[1].tabIndex, 0);
  assert.equal(tabs[0].tabIndex, -1);
  assert.equal(document.getElementById('demoSummary').hidden, true);
  assert.equal(document.getElementById('demoQuiz').hidden, false);
  assert.equal(keydown(tabs[1], 'Home'), true);
  assert.equal(tabs[0].getAttribute('aria-selected'), 'true');
  assert.equal(keydown(tabs[0], 'End'), true);
  assert.equal(tabs[1].getAttribute('aria-selected'), 'true');
  assert.equal(keydown(tabs[1], 'ArrowRight'), true);
  assert.equal(tabs[0].getAttribute('aria-selected'), 'true');
  assert.equal(keydown(tabs[0], 'ArrowLeft'), true);
  assert.equal(tabs[1].getAttribute('aria-selected'), 'true');
  assert.equal(keydown(options[0], 'ArrowRight'), false);
  assert.equal(keydown(tabs[0], 'Enter'), false);

  const beforeForeignClick = snapshot();
  click(new Element({'data-demo-answer':'0'}));
  assert.deepEqual(snapshot(), beforeForeignClick, 'answers outside sample are ignored');
  click(nested(options[1]));
  const wrong = snapshot();
  assert.equal(wrong.feedback, `✕ ${translator.keyCatalog['homeDemo.incorrect'][index]} · ${translator.keyCatalog['homeDemo.a'][index]}`);
  assert.equal(wrong.resetHidden, false);
  assert.ok(wrong.answers.every(option => option.disabled));
  assert.ok(wrong.answers[0].classes.includes('is-correct'));
  assert.ok(wrong.answers[1].classes.includes('is-wrong'));
  assert.equal(wrong.answers[1].pressed, 'true');
  click(options[0]);
  assert.deepEqual(snapshot(), wrong, 'a question can only be answered once');
  click(tabs[0]);
  click(tabs[1]);
  assert.deepEqual(snapshot(), wrong, 'switching tabs preserves the sample answer');

  click(nested(document.getElementById('demoReset')));
  assert.equal(snapshot().feedback, '');
  assert.equal(snapshot().resetHidden, true);
  assert.ok(snapshot().answers.every(option => !option.disabled && option.pressed === null && !option.classes.some(name => name.startsWith('is-'))));
  assert.equal(options[0].focusCount, 1);
  click(nested(options[0]));
  assert.equal(snapshot().feedback, `✓ ${translator.keyCatalog['homeDemo.correct'][index]} · ${translator.keyCatalog['homeDemo.a'][index]}`);
  assert.ok(!snapshot().answers.some(option => option.classes.includes('is-wrong')));
  results.push(language);
}

// Shared loading cannot attach quiz handlers to authenticated/workspace pages.
let pageQueries = 0;
vm.runInNewContext(demoSource, {document:{querySelector(selector) { pageQueries++; assert.equal(selector, '.home-page [data-study-demo]'); return null; }}});
assert.equal(pageQueries, 1);
console.log(JSON.stringify({languages:results, catalogKeys:keys.length}));
"""


@pytest.mark.skipif(NODE is None, reason="Node.js is required for home sample runtime tests")
def test_home_sample_is_localized_prerendered_interactive_and_isolated() -> None:
    completed = subprocess.run(
        [NODE, "-e", NODE_HARNESS, str(ROOT)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    result = json.loads(completed.stdout.strip())
    assert result == {
        "languages": ["tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi"],
        "catalogKeys": 9,
    }
