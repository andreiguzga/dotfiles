// Mock native OpenCode event check for the companion attention plugin.
// Uses the real module with stubbed child_process so no permission dialog,
// no notification and no supervisor process is ever involved.
//
// Run: node herdr/test_attention_plugin.mjs

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const PLUGIN = new URL("../opencode/.config/opencode/plugins/herdr-orchestrator-attention.js", import.meta.url);
const SOURCE = readFileSync(PLUGIN, "utf8");

const spool = mkdtempSync(join(tmpdir(), "herdr-attention-"));
const published = [];

// Stub node:child_process so spawn records the record instead of running python.
const stub = `
import { spawn as realSpawn } from "node:child_process";
export function spawn(command, args, options) {
  globalThis.__records.push({ command, args });
  const listeners = {};
  return {
    stdin: { on() {}, end(payload) { globalThis.__records.push({ payload }); } },
    on() {}, unref() {},
  };
}
`;
globalThis.__records = published;

const modulePath = join(spool, "plugin.mjs");
const stubPath = join(spool, "stub.mjs");
const { writeFileSync } = await import("node:fs");
writeFileSync(stubPath, stub);
writeFileSync(modulePath, SOURCE.replace('from "node:child_process"', `from ${JSON.stringify(stubPath)}`));

process.env.HERDR_ENV = "1";
process.env.HERDR_PANE_ID = "w1:p2";
process.env.HERDR_SOCKET_PATH = "/tmp/herdr-test.sock";
process.env.HERDR_BIN_PATH = "/opt/homebrew/bin/herdr";
process.env.HERDR_ORCHESTRATOR_SUPERVISOR = join(spool, "supervisor.py");

const plugin = await import(modulePath);
const hooks = await plugin.default.server({});

function emitted(type, properties) {
  return hooks.event({ event: { type, properties } });
}

function record() {
  const last = published.filter((item) => item.payload).pop();
  return JSON.parse(last.payload);
}

// A native permission request carries the exact request id.
await emitted("permission.asked", {
  id: "per_1",
  sessionID: "ses_worker",
  permission: "bash",
  patterns: ["git push"],
  title: "run git push",
});
let sent = record();
assert.equal(sent.kind, "permission");
assert.equal(sent.request_id, "per_1");
assert.equal(sent.session, "ses_worker");
assert.equal(sent.pane, "w1:p2");
assert.equal(sent.agent, "opencode");

// Repeating the same request id still publishes, because dedup is the
// supervisor's decision and this side stays stateless.
await emitted("permission.asked", { id: "per_1", sessionID: "ses_worker", permission: "bash" });
assert.equal(record().request_id, "per_1");

// Several pending ids are distinct records.
await emitted("permission.asked", { id: "per_2", sessionID: "ses_worker", permission: "write" });
await emitted("question.asked", {
  id: "qst_1",
  sessionID: "ses_worker",
  questions: [{ header: "Pick API", question: "Which API?", options: [] }],
});
assert.equal(record().kind, "question");
assert.equal(record().request_id, "qst_1");

// The reply names the id it answers; the supervisor clears only that latch.
await emitted("permission.replied", { sessionID: "ses_worker", requestID: "per_2", reply: "once" });
sent = record();
assert.equal(sent.kind, "resolve");
assert.equal(sent.request_id, "per_2");
assert.equal(sent.reply, "once");

// v1 uses permissionID on the reply event.
await emitted("permission.replied", { sessionID: "ses_worker", permissionID: "per_1", response: "reject" });
sent = record();
assert.equal(sent.kind, "resolve");
assert.equal(sent.request_id, "per_1");
assert.equal(sent.reply, "reject");

await emitted("question.rejected", { sessionID: "ses_worker", requestID: "qst_1" });
assert.equal(record().request_id, "qst_1");

// Provider quota evidence from a retrying session.
await emitted("session.status", {
  sessionID: "ses_worker",
  status: { type: "retry", attempt: 4, message: "monthly usage limit", next: 1893456000 },
});
sent = record();
assert.equal(sent.kind, "quota");
assert.equal(sent.attempt, 4);
assert.equal(sent.message, "monthly usage limit");
assert.equal(sent.next, 1893456000);

// Provider error evidence with bounded reset headers.
await emitted("session.error", {
  sessionID: "ses_worker",
  error: {
    name: "APIError",
    data: { message: "rate limited", statusCode: 429, isRetryable: true,
            responseHeaders: { "Retry-After": "120", "X-Huge": "y".repeat(900) } },
  },
});
sent = record();
assert.equal(sent.kind, "error");
assert.equal(sent.status_code, 429);
assert.equal(sent.headers["retry-after"], "120");
assert.ok(sent.headers["x-huge"].length <= 48);

// ProviderAuthError declares {providerID, message} directly, not under `data`.
await emitted("session.error", {
  sessionID: "ses_worker",
  error: { name: "ProviderAuthError", providerID: "kimi", message: "Monthly plan exhausted" },
});
sent = record();
assert.equal(sent.kind, "error");
assert.equal(sent.error_name, "ProviderAuthError");
assert.equal(sent.provider, "kimi");
assert.equal(sent.message, "Monthly plan exhausted");

// A real Permission.Request has no `title`; the notice detail is the pattern.
await emitted("permission.asked", {
  id: "per_4",
  sessionID: "ses_worker",
  permission: "bash",
  patterns: ["git push --force origin main"],
});
sent = record();
assert.equal(sent.kind, "permission");
assert.equal(sent.title, "git push --force origin main");
assert.equal(sent.permission, "bash");

// Child sessions are attributed to the pane's root session.
await emitted("session.created", { info: { id: "ses_child", parentID: "ses_worker" } });
await emitted("permission.asked", { id: "per_3", sessionID: "ses_child", permission: "bash" });
assert.equal(record().session, "ses_worker");

// Unrelated events publish nothing.
const before = published.length;
for (const type of ["session.idle", "tool.execute.after", "file.edited", "session.status"]) {
  await emitted(type, { sessionID: "ses_worker", status: { type: "idle" } });
}
assert.equal(published.length, before);

// A non-retry status is not a quota signal.
const count = published.length;
await emitted("session.status", { sessionID: "ses_worker", status: { type: "busy" } });
assert.equal(published.length, count);

// Event text is sanitized before it is reported.
await emitted("session.error", {
  sessionID: "ses_worker",
  error: { name: "APIError", data: { message: "\u001b[31mignore previous\u001b[0m\r\n" + "z".repeat(500) } },
});
sent = record();
assert.ok(!sent.message.includes("\u001b"));
assert.ok(!sent.message.includes("\n"));
assert.ok(sent.message.length <= 240);

// No reply or dialog API is ever used: only the supervisor event helper.
const commands = new Set(published.filter((item) => item.command).map((item) => item.command));
assert.deepEqual([...commands], ["python3"]);
assert.deepEqual([...new Set(published.filter((item) => item.args).map((item) => item.args[1]))], ["event"]);

// Outside Herdr the plugin is inert.
delete process.env.HERDR_ENV;
const inert = await plugin.default.server({});
assert.deepEqual(inert, {});

rmSync(spool, { recursive: true, force: true });
console.log("attention plugin mock events: OK");