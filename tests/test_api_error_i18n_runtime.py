"""API error text is shown in the reader's language, checked offline with node:vm."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for API error runtime checks")

HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = process.argv[1];
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const i18nSource = read('frontend/i18n.js');
const languages = ['tr', 'en', 'de', 'fr', 'es', 'it', 'pt', 'ru', 'ar', 'zh', 'ja', 'ko', 'hi'];

function runtime(pathname, source = i18nSource, saved = null) {
  const element = () => ({style:{}, dataset:{}, setAttribute() {}, append() {}, remove() {}});
  const document = {
    documentElement:{lang:'tr', dir:'ltr'}, body:{dataset:{}}, head:{append() {}}, title:'LectureSift',
    querySelector:() => null, querySelectorAll:() => [], createElement:element,
    createTreeWalker:() => ({nextNode:() => false}),
  };
  const context = vm.createContext({
    document, window:{}, NodeFilter:{SHOW_TEXT:4}, URL, navigator:{language:'tr'},
    location:{pathname, href:`https://lecturesift.com${pathname}`, origin:'https://lecturesift.com', hostname:'lecturesift.com', search:'', hash:''},
    localStorage:{getItem:key => key === 'lecturesift-ui' ? saved : null, setItem() {}},
  });
  vm.runInContext(source, context);
  return context;
}

const sourceMinutes = 'Bu kaynak yaklaşık 42 dakika; hesabında 7 dakika kaldı. Daha kısa bir kaynak yükle veya dakika hakkını artır.';
const expected = {
  en: {
    login:'Incorrect email or password.',
    minutes:'This source is about 42 minutes long; your account has 7 minutes left. Upload a shorter source or add more minutes.',
    planFiles:'The Free plan allows up to 3 sources per job.',
    guestJob:'This job is about 45 minutes; the guest plan allows 30 minutes per job. Split the source or upgrade your plan.',
    guestMax:'The no-account trial accepts a source of up to 30 minutes.',
    fields:'To start the payment, complete the details the payment provider requires: phone, billing address.',
  },
  de: {
    login:'E-Mail-Adresse oder Passwort ist falsch.',
    minutes:'Diese Quelle dauert etwa 42 Minuten; auf deinem Konto sind noch 7 Minuten übrig. Lade eine kürzere Quelle hoch oder erhöhe dein Minutenkontingent.',
    planFiles:'Im Tarif Kostenlos sind bis zu 3 Quellen pro Auftrag möglich.',
    guestJob:'Dieser Auftrag dauert etwa 45 Minuten; der Tarif guest erlaubt 30 Minuten pro Auftrag. Teile die Quelle oder wechsle zu einem höheren Tarif.',
    guestMax:'Der Test ohne Konto ist für eine Quelle bis 30 Minuten möglich.',
    fields:'Ergänze die vom Zahlungsanbieter verlangten Angaben, um die Zahlung zu starten: Telefon, Rechnungsadresse.',
  },
  ar: {
    login:'البريد الإلكتروني أو كلمة المرور غير صحيحة.',
    minutes:'مدة هذا المصدر نحو 42 دقيقة، وتبقّى في حسابك 7 دقيقة. ارفع مصدرًا أقصر أو زد رصيد الدقائق.',
    planFiles:'تسمح خطة مجاني بحد أقصى 3 من المصادر لكل مهمة.',
    guestJob:'مدة هذه المهمة نحو 45 دقيقة، بينما تسمح خطة guest بـ 30 دقيقة لكل مهمة. قسّم المصدر أو رقِّ خطتك.',
    guestMax:'تقبل التجربة بدون حساب مصدرًا مدته 30 دقيقة كحد أقصى.',
    fields:'لبدء الدفع، أكمل البيانات التي يطلبها مزود الدفع: الهاتف، عنوان الفوترة.',
  },
};
const messages = {
  login:'E-posta veya parola hatalı.',
  minutes:sourceMinutes,
  planFiles:'free planında bir işe en fazla 3 kaynak eklenebilir.',
  guestJob:'Bu iş yaklaşık 45 dakika; guest planında tek iş sınırı 30 dakikadır. Kaynağı böl veya planını yükselt.',
  guestMax:'Hesapsız deneme en fazla 30 dakikalık bir kaynakla kullanılabilir.',
  fields:'Ödemeyi başlatmak için ödeme sağlayıcısının istediği bilgileri tamamla: telefon, fatura adresi.',
};

const checked = [];
for (const [language, page] of [['en', '/en/workspace.html'], ['de', '/de/account.html'], ['ar', '/ar/login.html']]) {
  const central = runtime(page).window.LectureSiftI18n;
  assert.equal(central.language, language);
  for (const [name, message] of Object.entries(messages)) {
    assert.equal(central.apiMessage(message), expected[language][name], `${language} ${name}`);
    // Already-localized text and repeated calls stay stable.
    assert.equal(central.apiMessage(expected[language][name]), expected[language][name], `${language} ${name} twice`);
  }
  assert.equal(central.apiMessage('Synthetic server rejection'), 'Synthetic server rejection');
  assert.equal(central.apiMessage('Bu kaynak yaklaşık 42 dakika; başka bir şey.'), 'Bu kaynak yaklaşık 42 dakika; başka bir şey.');
  assert.equal(central.apiMessage(''), '');
  assert.equal(central.apiMessage(undefined), undefined);
  checked.push(language);
}

// The Turkish site keeps the API's own wording.
const turkish = runtime('/workspace.html', i18nSource, 'tr').window.LectureSiftI18n;
assert.equal(turkish.language, 'tr');
for (const message of Object.values(messages)) assert.equal(turkish.apiMessage(message), message);

// Every api.* translation keeps the Turkish row's placeholders.
const rows = {};
for (const match of i18nSource.matchAll(/^\s*"((?:api|rollout)\.[^"]+)":(\[.*\]),?$/gm)) rows[match[1]] = JSON.parse(match[2]);
const apiKeys = Object.keys(rows).filter(key => key.startsWith('api.'));
assert.ok(apiKeys.length > 100, `api rows: ${apiKeys.length}`);
for (const key of apiKeys.concat(['rollout.guestNoteMinutes', 'rollout.guestUsedMinutes', 'rollout.singleUseMinutes'])) {
  const values = rows[key];
  assert.equal(values?.length, languages.length, key);
  const names = (values[0].match(/\{\w+\}/g) || []).sort().join();
  for (const value of values) assert.equal((value.match(/\{\w+\}/g) || []).sort().join(), names, `${key}: ${value}`);
}

// Public pages run the builder's three-column runtime; API text still translates there.
const builder = read('scripts/build_localized_site.mjs');
const begin = builder.indexOf('function publicRuntimeSource(');
const end = builder.indexOf('\nfunction readHeadValue', begin);
assert.ok(begin >= 0 && end > begin, 'publicRuntimeSource extraction');
const publicSource = vm.runInNewContext(`${builder.slice(begin, end)}; publicRuntimeSource('de')`, {LANGUAGES:languages, dynamicCopySource:i18nSource, JSON});
assert.ok(publicSource.length < i18nSource.length * 0.5);
const publicCentral = runtime('/de/plans', publicSource).window.LectureSiftI18n;
assert.equal(publicCentral.apiMessage('Geçersiz ödeme dönemi.'), 'Ungültiger Abrechnungszeitraum.');
assert.equal(publicCentral.apiMessage(messages.fields), expected.de.fields);
assert.equal(publicCentral.apiMessage('Synthetic server rejection'), 'Synthetic server rejection');

// Guest trial copy follows guest_trial_minutes and falls back to the stored 30-minute text.
const rollout = read('frontend/rollout.js');
const copyBegin = rollout.indexOf('  function guestTrialCopy(');
const copyEnd = rollout.indexOf('\n  function renderGuestTrialCopy', copyBegin);
assert.ok(copyBegin >= 0 && copyEnd > copyBegin, 'guestTrialCopy extraction');
const guestCopy = {};
for (const [language, page] of [['tr', '/workspace.html'], ['en', '/en/workspace.html'], ['ar', '/ar/workspace.html']]) {
  const context = runtime(page, i18nSource, 'tr');
  const central = context.window.LectureSiftI18n;
  context.rt = (key, fallback) => central.t(key) || fallback || key;
  context.rolloutLocale = () => central.locale;
  const copy = vm.runInContext(`let billingCatalog = null; ${rollout.slice(copyBegin, copyEnd)}; ({copy:guestTrialCopy, set:value => { billingCatalog = value; }})`, context);
  for (const key of ['rollout.guestNote', 'rollout.guestUsed', 'rollout.singleUse']) {
    assert.equal(copy.copy(key, 'fallback'), central.t(key), `${language} ${key} without catalog`);
    copy.set({guest_trial_minutes:0});
    assert.equal(copy.copy(key, 'fallback'), central.t(key), `${language} ${key} with zero minutes`);
    copy.set({guest_trial_minutes:30});
    if (language !== 'ar') assert.equal(copy.copy(key, 'fallback'), central.t(key), `${language} ${key} at 30 minutes`);
    copy.set({guest_trial_minutes:12});
    const twelve = copy.copy(key, 'fallback');
    assert.ok(!twelve.includes('{minutes}') && twelve !== central.t(key), `${language} ${key} at 12 minutes`);
    guestCopy[`${language}:${key}`] = twelve;
    copy.set(null);
  }
}
assert.equal(guestCopy['en:rollout.guestNote'], 'No-account trial: process one source of up to 12 minutes on this device. Create a free account for longer jobs.');
assert.equal(guestCopy['tr:rollout.guestUsed'], 'Tek seferlik 12 dakikalık denemen kullanıldı. Devam etmek için ücretsiz hesap oluştur.');
assert.ok(guestCopy['ar:rollout.singleUse'].includes('١٢') || guestCopy['ar:rollout.singleUse'].includes('12'));

// The workspace reads a guest file's length in the browser before upload.
const app = read('frontend/app.js');
const mediaBegin = app.indexOf('function mediaDurationSeconds(');
const mediaEnd = app.indexOf('\nfunction selectedSourceFiles', mediaBegin);
assert.ok(mediaBegin >= 0 && mediaEnd > mediaBegin, 'media helper extraction');
const created = [], revoked = [], timeouts = [];
const durations = {'long.mp3':1900, 'exact.mp3':1803, 'over.mp3':1804, 'short.wav':300, 'lecture.mp4':2400, 'stream.webm':Infinity};
const mediaContext = vm.createContext({
  VIDEO_EXTENSIONS:new Set(['mp4', 'webm']), MEDIA_EXTENSIONS:new Set(['mp3', 'wav', 'mp4', 'webm']),
  fileExtension:file => file.name.split('.').pop(),
  URL:{createObjectURL:file => `blob:${file.name}`, revokeObjectURL:url => revoked.push(url)},
  setTimeout:(callback, ms) => { timeouts.push(ms); return setImmediate(callback); }, clearTimeout:handle => clearImmediate(handle),
  billingCatalog:null, billingAccount:null,
  document:{createElement(tag) {
    const element = {tag, attributes:{}, removeAttribute(name) { delete this.attributes[name]; delete this._src; }};
    Object.defineProperty(element, 'src', {get() { return this._src; }, set(url) {
      this._src = url;
      this.attributes.src = url;
      const name = url.slice(5);
      if (name in durations) queueMicrotask(() => { this.duration = durations[name]; this.onloadedmetadata?.(); });
      else if (name.startsWith('broken')) queueMicrotask(() => this.onerror?.());
    }});
    created.push(element);
    return element;
  }},
});
vm.runInContext(app.slice(mediaBegin, mediaEnd), mediaContext);
(async () => {
  const seconds = name => vm.runInContext(`mediaDurationSeconds({name:${JSON.stringify(name)}})`, mediaContext);
  assert.equal(await seconds('long.mp3'), 1900);
  assert.equal(created.at(-1).tag, 'audio');
  assert.equal(created.at(-1).attributes.src, undefined);
  assert.equal(await seconds('lecture.mp4'), 2400);
  assert.equal(created.at(-1).tag, 'video');
  assert.equal(await seconds('broken.mp3'), null);
  assert.equal(await seconds('silent.mp3'), null);
  assert.equal(await seconds('stream.webm'), null);
  assert.deepEqual(revoked, ['blob:long.mp3', 'blob:lecture.mp4', 'blob:broken.mp3', 'blob:silent.mp3', 'blob:stream.webm']);
  assert.ok(timeouts.every(ms => ms === 3000));

  const over = async (files, catalog, plan = 'guest') => {
    mediaContext.billingCatalog = catalog;
    mediaContext.billingAccount = plan ? {plan:{code:plan}} : null;
    return vm.runInContext(`guestMediaOverLimit(${JSON.stringify(files.map(name => ({name})))})`, mediaContext);
  };
  const before = created.length;
  assert.equal(await over(['long.mp3'], {guest_trial_minutes:30}), 30);
  assert.equal(await over(['over.mp3'], {guest_trial_minutes:30}), 30);
  assert.equal(await over(['exact.mp3'], {guest_trial_minutes:30}), null);
  assert.equal(await over(['short.wav'], {guest_trial_minutes:30}), null);
  assert.equal(await over(['short.wav'], {guest_trial_minutes:4}), 4);
  assert.equal(await over(['long.mp3', 'broken.mp3'], {guest_trial_minutes:30}), null);
  assert.equal(await over(['silent.mp3'], {guest_trial_minutes:30}), null);
  const reads = created.length - before;
  assert.equal(await over(['long.mp3'], {guest_trial_minutes:30}, 'free'), null);
  assert.equal(await over(['long.mp3'], {guest_trial_minutes:30}, null), null);
  assert.equal(await over(['long.mp3'], {}), null);
  assert.equal(await over(['long.mp3'], {guest_trial_minutes:'none'}), null);
  assert.equal(await over(['notes.pdf'], {guest_trial_minutes:30}), null);
  assert.equal(created.length - before, reads, 'no media read without a guest limit or media file');

  // The workspace plans loader can be the first to deliver the catalog; it refreshes the guest copy then.
  const plansBegin = rollout.indexOf('  async function installWorkspacePlans() {');
  const plansEnd = rollout.indexOf('\n  async function installWorkspace()', plansBegin);
  assert.ok(plansBegin >= 0 && plansEnd > plansBegin, 'installWorkspacePlans extraction');
  const plansErrors = [];
  const plansContext = vm.createContext({
    $:id => id === 'plansGrid' ? {innerHTML:'', querySelectorAll:() => []} : null,
    document:{querySelector:() => ({})},
    selectedCurrency:() => 'TRY',
    api:async () => ({plans:[], guest_trial_minutes:12}),
    rt:(key, fallback) => fallback || key,
    workspacePlanCard:() => '', openLocalizedCheckout:() => {},
    showInlineMessage:message => plansErrors.push(message),
  });
  const plans = vm.runInContext(`let billingCatalog = null, billingAccount = {plan:{code:'guest'}}; const rendered = [];
    function renderGuestTrialCopy() { rendered.push(billingCatalog?.guest_trial_minutes ?? null); }
    ${rollout.slice(plansBegin, plansEnd)}; ({install:installWorkspacePlans, rendered})`, plansContext);
  await plans.install();
  assert.deepEqual(plansErrors, []);
  assert.deepEqual(plans.rendered, [12], 'guest copy re-rendered with the catalog from the workspace plans loader');
  console.log(JSON.stringify({languages:checked, apiRows:apiKeys.length, guestCopy}));
})().catch(error => { console.error(error); process.exit(1); });
"""


def _run_harness() -> dict:
    completed = subprocess.run(
        [NODE, "-e", HARNESS, str(ROOT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_api_errors_follow_the_reader_language_and_keep_dynamic_values() -> None:
    result = _run_harness()
    assert result["languages"] == ["en", "de", "ar"]
    assert result["apiRows"] > 100
    assert "12" in result["guestCopy"]["en:rollout.singleUse"]


def test_api_error_call_sites_use_the_shared_helper() -> None:
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "window.LectureSiftI18n?.apiMessage?.(message) ?? message" in app
    assert 'apiText(body?.detail?.message || body?.message)' in (FRONTEND / "auth.js").read_text(encoding="utf-8")
    assert "PLANS_I18N.apiMessage?.(" in (FRONTEND / "plans.js").read_text(encoding="utf-8")
    for name in ("rollout.js", "library.js", "support.js", "contact.js"):
        assert "apiMessage" in (FRONTEND / name).read_text(encoding="utf-8"), name


def test_guest_media_limit_is_checked_before_upload_and_offers_a_free_account() -> None:
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    handler = app[app.index('$("analyzeButton").onclick = async () => {'):app.index("async function pollJob()")]
    check = handler.index("await guestMediaOverLimit(uploadFiles)")
    assert check < handler.index("requestedJobLoaded = true;") < handler.index("new XMLHttpRequest()")
    assert '"api.guestTrialMaxMinutes"' in handler and '"LS-GUEST-04"' in handler
    assert "billingCatalog?.guest_trial_minutes" in app
    assert 'code === "LS-BILL-10" || code === "LS-GUEST-04"' in app
    assert 'link.id = "guestSourceLimitSignup"' in app
    assert '"rollout.createFreeAccount"' in app
