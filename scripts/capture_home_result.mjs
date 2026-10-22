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
// Three parts of the result screen are captured: the summary panel, the quiz
// question answered correctly and the flashcard turned to its answer. Each is
// rendered by the workspace at its own width and drawn at one zoom per image,
// so all text keeps the same scale. Each image is laid out at `size` CSS
// pixels with `gap` around and between the parts, and saved at `scale` pixels
// per CSS pixel; the file name carries its pixel width.
const VARIANTS = [
  // Desktop: the summary panel on the left at full height, its title on one
  // line; the quiz question and the flashcard stacked on its right, filling
  // the same height.
  {size: {width: 960, height: 560}, scale: 1.5, gap: 24, arrange: columns, summaryWidth: 400},
  // Phone: the summary panel across the top; the quiz question and the
  // flashcard side by side below it, the same height, at the largest zoom
  // that fits.
  {size: {width: 360, height: 640}, scale: 2, gap: 16, arrange: rows},
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
      const plan = await variant.arrange(variant, language, sample);
      const density = variant.scale * plan.zoom;
      const summary = await summaryAt(language, sample, plan.summaryWidth, density);
      const {quiz, cards} = await glimpseAt(language, sample, plan.glimpseWidth, density, plan.flashcardHeight);
      const shots = {summary, quiz, cards};
      const places = checkedPlaces(variant, plan.zoom, shots, plan.place(shots));
      const bytes = await compose(variant, plan.zoom, shots, places);
      const [width, height] = [variant.size.width * variant.scale, variant.size.height * variant.scale];
      const file = path.join(OUTPUT, `result-screen-v1-${language}-${width}.webp`);
      await writeFile(file, bytes);
      console.log(`${path.relative(ROOT, file)} ${width}x${height} zoom ${plan.zoom.toFixed(3)} ${bytes.length} bytes`);
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

async function newContext(language, width, scale) {
  const context = await browser.newContext({
    baseURL: ORIGIN, viewport: {width, height: 1000}, isMobile: true, hasTouch: true, deviceScaleFactor: scale,
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
  const context = await newContext(language, 1280, 1);
  await context.route("**/*", route => new URL(route.request().url()).origin === ORIGIN ? route.continue() : route.abort("blockedbyclient"));
  const page = await context.newPage();
  await page.goto(language === "tr" ? "/" : `/${language}/`);
  const sample = await page.locator("[data-study-demo]").evaluate(host => {
    const text = selector => host.querySelector(selector).textContent.trim();
    const choices = [...host.querySelectorAll("#demoQuiz [data-demo-answer]")];
    return {
      topic: text(".demo-subject"),
      idea: text(".demo-concept h3"),
      insight: text(".demo-insight"),
      connection: text(".demo-takeaway p"),
      question: text("#demoQuiz h3"),
      choices: choices.map(button => button.textContent.trim()),
      correct: choices.findIndex(button => button.dataset.demoAnswer === "0"),
      answer: text("#demoCardAnswer"),
    };
  });
  await context.close();
  return sample;
}

// The workspace restores a finished job and renders it as a result, in a
// viewport `width` CSS pixels wide.
async function openResult(language, sample, width, scale) {
  const result = {
    title: sample.topic, job_type: "study_pack",
    summary: `${sample.idea}\n\n${sample.insight}`,
    key_points: sample.connection.split(" · "),
    options: {include_summary: true, include_transcript: false, include_slides: false, quiz_count: 1, flashcard_count: 1},
    quiz: [{question: sample.question, options: sample.choices, answer_index: sample.correct, explanation: sample.insight}],
    // The homepage's own flashcard: the sample question and its answer.
    flashcards: [{front: sample.question, back: sample.answer}],
    artifacts: [], slides: [], transcript_segments: [],
  };
  const stubs = new Map([
    ["/billing/me", {account: {user: {id: "sample", email: "sample@example.invalid", email_verified: true}, plan: {code: "free"}, remaining_minutes: 120, used_minutes: 0, credit_minutes: 0}}],
    [`/jobs/${JOB}`, {job_id: JOB, status: "done", stage: "done", percent: 100, source_type: "media", options: {job_type: "study_pack"}}],
    [`/jobs/${JOB}/result`, result],
  ]);
  const context = await newContext(language, width, scale);
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
  // "1 Quiz · 1 Flashcards" says little with counts of one.
  await page.locator("#resultMeta").evaluate(meta => { meta.style.display = "none"; });
  return {page, context};
}

// Hides the tabs from the first one that does not fit the row completely.
async function trimTabs(page) {
  await page.evaluate(() => {
    const row = document.querySelector(".result-tabs");
    const style = getComputedStyle(row);
    const edge = row.getBoundingClientRect().right - parseFloat(style.paddingRight) - parseFloat(style.borderRightWidth);
    let cut = false;
    for (const tab of row.querySelectorAll(".result-tab")) {
      if (getComputedStyle(tab).display === "none") continue;
      cut ||= tab.getBoundingClientRect().right > edge + 0.5;
      if (cut) tab.style.display = "none";
    }
  });
}

// Opens the result with the viewport chosen so that `selector` is `width`
// CSS pixels wide; `prepare` readies the page before it is measured.
async function openAtWidth(language, sample, selector, width, scale, prepare) {
  let viewport = Math.round(width) + 40;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const {page, context} = await openResult(language, sample, viewport, scale);
    await prepare(page);
    const actual = (await page.locator(selector).boundingBox()).width;
    if (Math.abs(actual - width) < 1) return {page, context};
    await context.close();
    viewport += Math.round(width - actual);
  }
  throw new Error(`No viewport makes ${selector} ${width}px wide`);
}

// Screenshot of `selector` down to the bottom of `until`, with its size in CSS pixels.
async function shot(page, selector, until = selector) {
  await page.locator(selector).scrollIntoViewIfNeeded();
  const box = await page.locator(selector).boundingBox();
  const end = await page.locator(until).boundingBox();
  const radius = await page.locator(selector).evaluate(node => parseFloat(getComputedStyle(node).borderTopLeftRadius));
  const clip = {x: box.x, y: box.y, width: box.width, height: end.y + end.height - box.y};
  if (clip.y < 0 || clip.y + clip.height > page.viewportSize().height) throw new Error(`${selector} does not fit the viewport`);
  return {png: await page.screenshot({clip}), width: clip.width, height: clip.height, radius};
}

// The summary panel: header, tabs, summary and key points, `width` CSS pixels
// wide, with the number of lines its title takes.
async function summaryAt(language, sample, width, scale) {
  const {page, context} = await openAtWidth(language, sample, "#results", width, scale, trimTabs);
  try {
    const titleLines = await page.locator("#resultHeading").evaluate(heading => {
      const range = document.createRange();
      range.selectNodeContents(heading);
      return new Set([...range.getClientRects()].map(line => Math.round(line.top))).size;
    });
    return {...await shot(page, "#results", ".result-body"), titleLines};
  } finally {
    await context.close();
  }
}

// The quiz question answered correctly, `width` CSS pixels wide, and, when
// `flashcardHeight` is given, the flashcard turned to its answer at the same
// width and flashcardHeight(quiz height) tall.
async function glimpseAt(language, sample, width, scale, flashcardHeight) {
  const {page, context} = await openAtWidth(language, sample, ".quiz-item", width, scale, page => page.locator("#resultTab-quiz").click());
  try {
    await page.locator(`.quiz-item[data-question="0"] [data-option="${sample.correct}"]`).click();
    await page.locator("#quiz-feedback-0").waitFor();
    const quiz = await shot(page, '.quiz-item[data-question="0"]');
    if (!flashcardHeight) return {quiz};
    await page.locator("#resultTab-cards").click();
    await page.locator("#activeFlash").click();
    await page.locator("#activeFlash p").filter({hasText: sample.answer}).waitFor();
    // The card centres its answer, so it can take any height above its content.
    const requested = flashcardHeight(quiz.height);
    await page.locator("#activeFlash").evaluate((card, height) => { card.style.minHeight = `${height}px`; }, requested);
    const cards = await shot(page, "#activeFlash");
    if (requested && Math.abs(cards.height - requested) > 1) throw new Error(`${language}: the flashcard needs ${cards.height}px, more than ${requested}px`);
    return {quiz, cards};
  } finally {
    await context.close();
  }
}

// Desktop: the widest summary panel, from `summaryWidth` down, whose title
// fits on one line and which leaves room for the flashcard. The zoom makes
// the panel fill the height; the quiz takes the rest of the width and the
// flashcard the rest of that column.
async function columns(variant, language, sample) {
  const {size: {width, height}, gap} = variant;
  for (let summaryWidth = variant.summaryWidth; summaryWidth >= variant.summaryWidth - 80; summaryWidth -= 10) {
    const summary = await summaryAt(language, sample, summaryWidth, 1);
    if (summary.titleLines > 1) continue;
    const zoom = (height - 2 * gap) / summary.height;
    const glimpseWidth = (width - 3 * gap) / zoom - summary.width;
    const flashcard = quizHeight => (height - 3 * gap) / zoom - quizHeight;
    // With no height set, the flashcard shows how much room its answer needs.
    const {quiz, cards} = await glimpseAt(language, sample, glimpseWidth, 1, () => 0);
    if (flashcard(quiz.height) < cards.height) continue;
    return {
      zoom, summaryWidth: summary.width, glimpseWidth, flashcardHeight: flashcard,
      place: shots => {
        const right = 2 * gap + zoom * shots.summary.width;
        return {summary: [gap, gap], quiz: [right, gap], cards: [right, 2 * gap + zoom * shots.quiz.height]};
      },
    };
  }
  throw new Error(`${language}: no summary width leaves room for the flashcard`);
}

// Phone: the largest zoom at which the summary panel and the two cards below
// it fit the height; what is left over splits evenly above and below.
async function rows(variant, language, sample) {
  const {size: {width, height}, gap} = variant;
  const widths = zoom => ({summary: (width - 2 * gap) / zoom, glimpse: (width - 3 * gap) / 2 / zoom});
  const used = async zoom => {
    const summary = await summaryAt(language, sample, widths(zoom).summary, 1);
    const {quiz} = await glimpseAt(language, sample, widths(zoom).glimpse, 1);
    return 3 * gap + zoom * (summary.height + quiz.height);
  };
  let [low, high] = [0.4, 1];
  if (await used(low) > height) throw new Error(`${language}: the phone image is too short even at zoom ${low}`);
  while (high - low > 0.004) {
    const middle = (low + high) / 2;
    if (await used(middle) <= height) low = middle; else high = middle;
  }
  const zoom = low;
  return {
    zoom, summaryWidth: widths(zoom).summary, glimpseWidth: widths(zoom).glimpse, flashcardHeight: quizHeight => quizHeight,
    place: shots => {
      const top = (height - gap - zoom * (shots.summary.height + shots.quiz.height)) / 2;
      const below = top + zoom * shots.summary.height + gap;
      return {summary: [gap, top], quiz: [gap, below], cards: [width - gap - zoom * shots.cards.width, below]};
    },
  };
}

// Every part lies inside the image, at least `gap` from its edges, and no
// part covers another.
function checkedPlaces(variant, zoom, shots, places) {
  const {size: {width, height}, gap} = variant;
  const boxes = Object.entries(places).map(([name, [x, y]]) => ({name, x, y, right: x + zoom * shots[name].width, bottom: y + zoom * shots[name].height}));
  for (const box of boxes) {
    if (box.x < gap - 1 || box.y < gap - 1 || box.right > width - gap + 1 || box.bottom > height - gap + 1) throw new Error(`${box.name} leaves the image margins`);
    for (const other of boxes) {
      if (other !== box && box.x < other.right && other.x < box.right && box.y < other.bottom && other.y < box.bottom) throw new Error(`${box.name} covers ${other.name}`);
    }
  }
  return places;
}

// Lays the three screenshots out on one canvas and encodes it as WebP with
// the browser's own encoder, so no other image tooling is needed.
async function compose(variant, zoom, shots, places) {
  const context = await browser.newContext({viewport: variant.size, deviceScaleFactor: variant.scale});
  const page = await context.newPage();
  const image = buffer => `data:image/png;base64,${buffer.toString("base64")}`;
  const figure = name => {
    const {png, width, height, radius} = shots[name];
    const [x, y] = places[name];
    return `<img src="${image(png)}" style="left:${x}px;top:${y}px;width:${width * zoom}px;height:${height * zoom}px;border-radius:${radius * zoom}px">`;
  };
  await page.setContent(`<!doctype html><style>
    html,body{margin:0;width:${variant.size.width}px;height:${variant.size.height}px;overflow:hidden}
    body{position:relative;background:transparent}
    img{position:absolute;display:block;box-shadow:0 2px 6px rgba(17,35,59,.12)}
  </style>${figure("summary")}${figure("quiz")}${figure("cards")}`);
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
