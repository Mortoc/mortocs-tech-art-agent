import { expect, test, type Page } from "@playwright/test";

/** The fake local model server inside the demo core: genuinely stopped/started, so "down" is connection refused. */
const LLM = "http://127.0.0.1:9124/llm";

test.beforeEach(async ({ request }) => {
  await request.post(`${LLM}/up`);
  // Every spec starts a fresh conversation; they share one core and one app.
  await request.post("http://127.0.0.1:9124/session/reset");
});

async function open(page: Page) {
  await page.goto("/?token=e2e");
  await expect(page.getByTestId("status")).toHaveCount(0);
  await expect(page.getByTestId("prompt")).toBeEnabled();
  await expect(page.getByTestId("send")).toHaveText("send");
}

async function say(page: Page, text: string) {
  await page.getByTestId("prompt").fill(text);
  await page.getByTestId("send").click();
}

test("connects to the app it belongs to and streams an answer from the model", async ({ page }) => {
  await open(page);
  await expect(page.getByTestId("welcome").locator("h2")).toHaveText("Blender Agent");
  await say(page, "add a cube");
  await expect(page.getByTestId("user-bubble")).toHaveText("add a cube");
  await expect(page.getByTestId("assistant-bubble")).toContainText("all good");
  await expect(page.getByTestId("notice")).toHaveCount(0);
});

test("a local model server that is not running says so plainly, then try again works once it is back", async ({ page, request }) => {
  await open(page);
  await request.post(`${LLM}/down`);

  await say(page, "add a cube");
  const notice = page.getByTestId("notice");
  await expect(notice).toBeVisible();
  await expect(notice).toHaveClass(/error/);
  await expect(notice).toContainText("Can't reach your local model server");
  await expect(notice).toContainText("ollama serve");
  await expect(notice).not.toContainText("internet");
  await expect(notice).not.toContainText("Traceback");
  // The pane is not stuck "working": the artist can type and send again.
  await expect(page.getByTestId("send")).toHaveText("send");
  await expect(page.getByTestId("prompt")).toBeEnabled();
  await expect(page.getByTestId("assistant-bubble")).toHaveCount(0);

  await request.post(`${LLM}/up`);
  await page.getByTestId("retry").click();
  await expect(page.getByTestId("assistant-bubble")).toContainText("all good");
  // The earlier failure stays in the log as history; the retry is the same message, sent again.
  await expect(page.getByTestId("user-bubble")).toHaveCount(2);
});

test("check understanding: a second opinion appears with findings, and a question can be answered in the message box", async ({ page }) => {
  await open(page);
  await expect(page.getByTestId("review-button")).toBeDisabled(); // nothing has been said yet
  await say(page, "add a cube");
  await expect(page.getByTestId("assistant-bubble")).toContainText("all good");

  await page.getByTestId("review-button").click();
  const review = page.getByTestId("review");
  await expect(review).toBeVisible();
  await expect(review).toHaveAttribute("data-verdict", "check");
  await expect(page.getByTestId("review-verdict")).toHaveText("worth a quick check");
  // Agent and reviewer are the same (fake) model in this harness, and the card says that this is the weaker check.
  await expect(page.getByTestId("review-reviewer")).toContainText("same model family");
  const finding = page.getByTestId("finding");
  await expect(finding).toHaveCount(1);
  await expect(finding.locator("blockquote")).toHaveText("add a cube");
  await expect(finding).toContainText("How big should the cube be");

  await page.getByTestId("use-question").click();
  await expect(page.getByTestId("prompt")).toHaveValue("How big should the cube be, and where should it go?");
  await expect(page.getByTestId("review-button")).toBeEnabled();
});

test("when the agent core itself goes away the pane says so, and after a restart it reconnects and the conversation is still there", async ({ page, request }) => {
  test.setTimeout(90_000);
  const CORE = "http://127.0.0.1:9124/core";
  await open(page);
  await say(page, "remember this one");
  await expect(page.getByTestId("user-bubble")).toHaveText("remember this one");
  await expect(page.getByTestId("assistant-bubble")).toHaveCount(1);
  await expect(page.getByTestId("assistant-bubble")).toContainText("all good");
  await expect(page.getByTestId("notice")).toHaveCount(0);

  await request.post(`${CORE}/down`);
  await expect(page.getByTestId("status")).toBeVisible({ timeout: 15_000 });

  await request.post(`${CORE}/up`);
  // The app and the pane reconnect by themselves (with backoff); no reload, no clicking.
  await expect(page.getByTestId("status")).toHaveCount(0, { timeout: 60_000 });
  await expect(page.getByTestId("prompt")).toBeEnabled({ timeout: 60_000 });
  await expect(page.getByTestId("user-bubble")).toHaveText("remember this one", { timeout: 60_000 });
  await expect(page.getByTestId("assistant-bubble")).toHaveCount(1);

  await say(page, "and again");
  await expect(page.getByTestId("user-bubble")).toHaveCount(2);
  await expect(page.getByTestId("assistant-bubble")).toHaveCount(2);
});
