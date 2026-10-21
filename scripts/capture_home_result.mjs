// Regenerates the homepage "what the result looks like" images from the real
// workspace result screen. The lesson is the homepage's own sample pack
// (Biology 101 · cellular energy), read from the built homepage in each
// language, so no new lesson text is written here. Every API response is a
// local stub; nothing leaves 127.0.0.1.
//
//   cd tests/browser && npm ci --ignore-scripts && npx playwright install chromium
//   node scripts/build_localized_site.mjs && node scripts/capture_home_result.mjs
//
// CHROMIUM_PATH=/path/to/chrome uses an existing browser instead. Text renders
// in whatever fonts the machine has; with Inter installed it matches the site.
import {spawn} from "node:child_process";
import {existsSync} from "node:fs";
import {mkdir, writeFile} from "node:fs/promises";
import {createRequire} from "node:module";
import path from "node:path";
import {fileURLToPath} from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUTPUT = path.join(ROOT, "frontend", "assets", "home");
const ORIGIN = "http://127.0.0.1:4173";
const API = "https://api.lecturesift.com";
const JOB = "home-sample-0001";
// Turkish for the Turkish homepage, English for the other twelve.
const LANGUAGES = ["tr", "en"];
// Each image is laid out at `size` CSS pixels and saved at `scale` pixels per
// CSS pixel; the file name carries its pixel width. The summary is captured at
// one viewport and the quiz question and flashcard at another; `glimpse.zoom`
// shrinks those two, and `layout` places all three given the summary's height.
const VARIANTS = [
  // Desktop: the summary window, with the quiz and flashcard on its right.
  {size: {width: 960, height: 560}, scale: 1.5, summary: {width: 900, mobile: false, until: ".result-body"}, glimpse: {width: 376, mobile: true, zoom: .86},
    layout: () => ".summary{left:24px;top:56px}.quiz{right:20px;top:24px}.cards{right:44px;bottom:24px}"},
  // Phone: the summary as a phone shows it, with the quiz and flashcard below.
  {size: {width: 360, height: 640}, scale: 2, summary: {width: 360, mobile: true, until: "#summaryContent", extra: 16}, glimpse: {width: 376, mobile: true, zoom: .54},
    layout: summary => `.summary{left:16px;top:16px}.quiz,.cards{top:${16 + summary.height + 8}px}.quiz{left:12px}.cards{right:12px}`},
];
const QUALITY = 0.72;

if (!existsSync(path.join(ROOT, "dist", "index.html"))) throw new Error("Run node scripts/build_localized_site.mjs first");
const {chromium} = createRequire(path.join(ROOT, "tests", "browser", "package.json"))("@playwright/test");

// The browser-test server serves this checkout's dist/ on a fixed port; never
// capture from some other checkout's server that already holds it.
if (await fetch(ORIGIN).then(() => true, () => false)) throw new Error(`${ORIGIN} is already in use; stop that server first`);
const server = spawn(process.execPath, [path.join(ROOT, "tests", "browser", "static-server.mjs")], {stdio: ["ignore", "ignore", "inherit"]});
let serverStopped = false;
server.once("exit", () => { serverStopped = true; });
const browser = await chromium.launch(process.env.CHROMIUM_PATH ? {executablePath: process.env.CHROMIUM_PATH} : {});
try {
  await waitForServer();
  await mkdir(OUTPUT, {recursive: true});
  for (const language of LANGUAGES) {
    const sample = await homepageSample(language);
    for (const variant of VARIANTS) {
      const {summary} = await resultShots(language, variant.summary, variant.scale, sample, ["summary"]);
      const {quiz, cards} = await resultShots(language, variant.glimpse, variant.scale, sample, ["quiz", "cards"]);
      const bytes = await compose(variant, {summary, quiz, cards});
      const [width, height] = [variant.size.width * variant.scale, variant.size.height * variant.scale];
      const file = path.join(OUTPUT, `result-screen-v1-${language}-${width}.webp`);
      await writeFile(file, bytes);
      console.log(`${path.relative(ROOT, file)} ${width}x${height} ${bytes.length} bytes`);
    }
  }
} finally {
  await browser.close();
  server.kill();
}

async function waitForServer() {
  for (let attempt = 0; attempt < 50 && !serverStopped; attempt += 1) {
    try { if ((await fetch(ORIGIN)).ok) return; } catch { /* not listening yet */ }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error("The local static server did not start");
}

async function newContext(language, {width, mobile}, scale) {
  const context = await browser.newContext({
    baseURL: ORIGIN, viewport: {width, height: 900}, isMobile: mobile, hasTouch: mobile, deviceScaleFactor: scale,
    colorScheme: "light", reducedMotion: "reduce", locale: language === "tr" ? "tr-TR" : "en-US",
  });
  await context.addInitScript(language => {
    localStorage.setItem("lecturesift-consent-v1", JSON.stringify({version: 1, necessary: true, analytics: false, advertising: false, updated_at: new Date().toISOString()}));
    localStorage.setItem("lecturesift-ui", language);
    localStorage.setItem("lecturesift-currency", "TRY");
  }, language);
  return context;
}

// The homepage sample pack, exactly as the homepage shows it in `language`.
async function homepageSample(language) {
  const context = await newContext(language, {width: 1280, mobile: false}, 1);
  await context.route("**/*", route => new URL(route.request().url()).origin === ORIGIN ? route.continue() : route.abort("blockedbyclient"));
  const page = await context.newPage();
  await page.goto(language === "tr" ? "/" : `/${language}/`);
  const sample = await page.locator("[data-study-demo]").evaluate(host => {
    const text = selector => host.querySelector(selector).textContent.trim();
    const choices = [...host.querySelectorAll("#demoQuiz [data-demo-answer]")];
    return {
      course: text(".visual-bar span").split(" · ").pop(),
      topic: text(".demo-subject"),
      idea: text(".demo-concept h3"),
      insight: text(".demo-insight"),
      connection: text(".demo-takeaway p"),
      question: text("#demoQuiz h3"),
      choices: choices.map(button => button.textContent.trim()),
      correct: choices.findIndex(button => button.dataset.demoAnswer === "0"),
    };
  });
  await context.close();
  return sample;
}

// The workspace restores a finished job and renders it as a result. `parts`
// picks the screenshots: the summary tab, the quiz question answered
// correctly, the first flashcard.
async function resultShots(language, viewport, scale, sample, parts) {
  const result = {
    title: `${sample.course} · ${sample.topic}`, job_type: "study_pack",
    summary: `${sample.idea}\n\n${sample.insight}`,
    key_points: sample.connection.split(" · "),
    options: {include_summary: true, include_transcript: false, include_slides: false, quiz_count: 1, flashcard_count: 1},
    quiz: [{question: sample.question, options: sample.choices, answer_index: sample.correct, explanation: sample.insight}],
    flashcards: [{front: sample.question, back: sample.choices[sample.correct]}],
    artifacts: [], slides: [], transcript_segments: [],
  };
  const stubs = new Map([
    ["/billing/me", {account: {user: {id: "sample", email: "sample@example.invalid", email_verified: true}, plan: {code: "free"}, remaining_minutes: 120, used_minutes: 0, credit_minutes: 0}}],
    [`/jobs/${JOB}`, {job_id: JOB, status: "done", stage: "done", percent: 100, source_type: "media", options: {job_type: "study_pack"}}],
    [`/jobs/${JOB}/result`, result],
  ]);
  const context = await newContext(language, viewport, scale);
  await context.addInitScript(() => localStorage.setItem("lecturesift-billing-token", "local-sample-token"));
  await context.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin === ORIGIN) return route.continue();
    if (url.origin !== API) return route.abort("blockedbyclient");
    const headers = {"Access-Control-Allow-Origin": ORIGIN, "Access-Control-Allow-Headers": "authorization, content-type", "Access-Control-Allow-Methods": "GET"};
    if (request.method() === "OPTIONS") return route.fulfill({status: 204, headers});
    const body = stubs.get(url.pathname);
    // Catalogs, ads and the like stay off; the result screen does not need them.
    return route.fulfill(body ? {headers, json: body} : {status: 404, headers, json: {detail: "Not available locally"}});
  });
  const page = await context.newPage();
  await page.goto(`${language === "tr" ? "" : `/${language}`}/workspace.html?job=${JOB}`);
  await page.locator("#resultHeading").filter({hasText: sample.topic}).waitFor();
  await page.evaluate(() => document.fonts.ready);
  // Screenshot of `selector`, cut off `extra` pixels below the bottom of `until`.
  const shot = async (selector, until = selector, extra = 0) => {
    await page.locator(selector).scrollIntoViewIfNeeded();
    const box = await page.locator(selector).boundingBox();
    const end = await page.locator(until).boundingBox();
    const radius = await page.locator(selector).evaluate(node => getComputedStyle(node).borderTopLeftRadius);
    const clip = {x: box.x, y: box.y, width: box.width, height: end.y + end.height + extra - box.y};
    return {png: await page.screenshot({clip}), width: clip.width, height: clip.height, radius};
  };
  const shots = {};
  if (parts.includes("summary")) shots.summary = await shot("#results", viewport.until, viewport.extra);
  if (parts.includes("quiz")) {
    await page.locator("#resultTab-quiz").click();
    await page.locator(`.quiz-item[data-question="0"] [data-option="${sample.correct}"]`).click();
    await page.locator("#quiz-feedback-0").waitFor();
    shots.quiz = await shot('.quiz-item[data-question="0"]');
  }
  if (parts.includes("cards")) {
    await page.locator("#resultTab-cards").click();
    shots.cards = await shot("#activeFlash");
  }
  await context.close();
  return shots;
}

// Lays the three screenshots out on one canvas and encodes it as WebP with
// the browser's own encoder, so no other image tooling is needed.
async function compose(variant, shots) {
  const context = await browser.newContext({viewport: variant.size, deviceScaleFactor: variant.scale});
  const page = await context.newPage();
  const image = buffer => `data:image/png;base64,${buffer.toString("base64")}`;
  const figure = (name, {png, width, height, radius}, zoom = 1) =>
    `<img class="${name}" src="${image(png)}" style="width:${width * zoom}px;height:${height * zoom}px;border-radius:${parseFloat(radius) * zoom}px">`;
  await page.setContent(`<!doctype html><style>
    html,body{margin:0;width:${variant.size.width}px;height:${variant.size.height}px;overflow:hidden}
    body{position:relative;background:transparent}
    img{position:absolute;display:block;box-shadow:0 2px 6px rgba(17,35,59,.12)}
    ${variant.layout(shots.summary)}
  </style>${figure("summary", shots.summary)}${figure("quiz", shots.quiz, variant.glimpse.zoom)}${figure("cards", shots.cards, variant.glimpse.zoom)}`);
  await page.evaluate(() => Promise.all([...document.images].map(img => img.decode())));
  const png = await page.screenshot({omitBackground: true});
  const webp = await page.evaluate(async ({source, quality}) => {
    const bitmap = await createImageBitmap(await (await fetch(source)).blob());
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    canvas.getContext("2d").drawImage(bitmap, 0, 0);
    const blob = await canvas.convertToBlob({type: "image/webp", quality});
    if (blob.type !== "image/webp") throw new Error("This browser cannot encode WebP");
    return [...new Uint8Array(await blob.arrayBuffer())];
  }, {source: image(png), quality: QUALITY});
  await context.close();
  return Buffer.from(webp);
}
