// Actual Source built UI over TCP, fed only by the original AI12 SQL outbox.
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium, expect } = require(path.join(process.env.AI12_SOURCE_ROOT, "frontend/node_modules/@playwright/test"));

(async () => {
  const control = process.env.AI12_CONTROL;
  const event = JSON.parse(await fs.readFile(path.join(control, "event.json"), "utf8"));
  const secret = await fs.readFile(path.join(control, "suggestion-credential"), "utf8");
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.goto((process.env.AI12_FRONTEND_URL || "http://127.0.0.1:4173") + "/recommendations");
    const panel = page.getByRole("region", { name: "AI suggestions", exact: true });
    await panel.getByLabel("Personal suggestion read credential").fill(secret);
    await panel.getByRole("button", { name: "Connect AI suggestions" }).click();
    await expect(panel.getByRole("heading", { name: "AI suggestion results" })).toBeVisible();
    await expect(panel.getByRole("row")).toHaveCount(2);
    await expect(panel).toContainText(event.payload.action);
    await expect(panel).toContainText(event.payload.product_id);
    await panel.getByRole("button", { name: "View suggestion evidence" }).click();
    const evidence = panel.getByRole("region", { name: "Suggestion evidence" });
    for (const key of ["recommendation_id", "candidate_id", "trace_id", "answer_id", "policy_sha256", "agent_config_version", "source_as_of", "expires_at"])
      await expect(evidence).toContainText(event.payload[key]);
    for (const reference of event.payload.evidence_refs) await expect(evidence).toContainText(reference);
    await expect(evidence).toContainText("Execution is not authorized");
    await expect(panel.getByRole("button", { name: /accept|execute|approve|reject/i })).toHaveCount(0);
    const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage }, cookies: document.cookie }));
    if (storage.includes(secret)) throw Error("credential persisted");
    const screenshot = process.env.AI12_SCREENSHOT;
    await panel.screenshot({ path: screenshot });
    const policyPath = path.join(control, "suggestion-access.json");
    const policy = JSON.parse(await fs.readFile(policyPath, "utf8"));
    policy.principals[0].credential_sha256 = "0".repeat(64);
    await fs.writeFile(policyPath, JSON.stringify(policy));
    await panel.getByRole("button", { name: "Refresh AI suggestions" }).click();
    await expect(panel.getByRole("alert")).toContainText("AI suggestion access was denied");
    await expect(panel.getByRole("table")).toHaveCount(0);
    await expect(panel.getByLabel("Personal suggestion read credential")).toHaveValue("");
    await fs.writeFile(path.join(control, "browser.json"), JSON.stringify({ status: "passed", exact_original_evidence: true, stored_credential: false, execution_controls: false, live_revocation: true }));
  } finally {
    await browser.close();
  }
})().catch(() => { process.stderr.write("ai12_browser_acceptance_failed\n"); process.exitCode = 1; });
