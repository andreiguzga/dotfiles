// Companion Herdr orchestrator attention plugin for OpenCode.
//
// Reports native approval/question requests and provider quota/error states for
// this pane to the orchestrator supervisor as bounded attention records. It does
// not report agent lifecycle state: the managed herdr-agent-state.js already owns
// pane.report_agent, and two writers on one pane would overwrite each other.
// It never answers a permission or question; those stay native dialogs.
//
// Records are plain JSON lines on the supervisor's stdin spool. The supervisor
// decides ownership, dedup and whether the user is notified, so an unregistered,
// replaced or finished pane produces no notice. Text from a harness event is
// treated as data only, never as instructions.

import { spawn } from "node:child_process";

const AGENT = "opencode";
const MAX_MESSAGE = 240;
const MAX_TITLE = 120;
const MAX_LIST = 8;

function bounded(value, limit = MAX_MESSAGE) {
  const raw = typeof value === "string" ? value : safeStringify(value);
  if (!raw) return undefined;
  // Event text is untrusted: strip escapes and control bytes, collapse
  // whitespace and cap the length. It is only ever reported, never executed.
  const clean = raw
    .replace(/\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)?/g, " ")
    .replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, " ")
    .replace(/[\u0000-\u001f\u007f-\u009f]/g, " ");
  const collapsed = clean.replace(/\s+/g, " ").trim();
  if (!collapsed) return undefined;
  return collapsed.length > limit ? `${collapsed.slice(0, limit - 3)}...` : collapsed;
}

function safeStringify(value) {
  try {
    return typeof value === "string" ? value : JSON.stringify(value);
  } catch {
    return undefined;
  }
}

function supervisorCommand() {
  const binary = process.env.HERDR_BIN_PATH || "herdr";
  const home = process.env.HOME || "";
  const script = process.env.HERDR_ORCHESTRATOR_SUPERVISOR
    || `${home}/.config/herdr/plugins/orchestrator/supervisor.py`;
  return { binary, script };
}

// Fire and forget: the harness must never wait on Herdr. A spawn failure is
// silent because the supervisor's periodic pass reconciles from agent state.
function send(record) {
  const { script } = supervisorCommand();
  const child = spawn("python3", [script, "event"], {
    stdio: ["pipe", "ignore", "ignore"],
    env: process.env,
  });
  child.on("error", () => {});
  child.stdin.on("error", () => {});
  child.stdin.end(`${JSON.stringify(record)}\n`);
  child.unref?.();
}

// Child sessions must not replace the pane's root session id.
const childSessions = new Map();

function rootSession(sessionID) {
  let current = sessionID;
  const seen = new Set();
  while (childSessions.has(current) && !seen.has(current)) {
    seen.add(current);
    current = childSessions.get(current);
  }
  return current;
}

function rememberParent(properties) {
  const info = properties?.info;
  if (info?.id && info?.parentID) {
    childSessions.set(info.id, info.parentID);
  }
}

function resetHeaders(value) {
  const headers = {};
  const source = value && typeof value === "object" ? value : {};
  for (const [key, headerValue] of Object.entries(source).slice(0, 16)) {
    const name = bounded(key, 64);
    const text = bounded(headerValue, 48);
    if (name && text) headers[name.toLowerCase()] = text;
  }
  return headers;
}

function quotaRecord(properties) {
  const status = properties?.status ?? {};
  const record = {
    kind: "quota",
    pane: process.env.HERDR_PANE_ID,
    agent: AGENT,
    attempt: typeof status.attempt === "number" ? status.attempt : undefined,
    message: bounded(status.message),
    next: typeof status.next === "number" ? status.next : undefined,
    headers: resetHeaders(properties?.headers ?? properties?.responseHeaders),
    source: "opencode:session.status",
  };
  return record;
}

function errorRecord(properties) {
  const error = properties?.error ?? {};
  // Not every error variant nests under `data`: ProviderAuthError is declared as
  // {providerID, message} directly, so both levels must be read or the notice
  // body comes out empty for the auth and quota case this exists to surface.
  const data = error?.data && typeof error.data === "object" ? error.data : {};
  return {
    kind: "error",
    pane: process.env.HERDR_PANE_ID,
    agent: AGENT,
    error_name: bounded(error?.name, 48),
    status_code: typeof data.statusCode === "number" ? data.statusCode : undefined,
    is_retryable: typeof data.isRetryable === "boolean" ? data.isRetryable : undefined,
    provider: bounded(data.providerID ?? error?.providerID, 40),
    message: bounded(data.message ?? error?.message),
    headers: resetHeaders(data.responseHeaders ?? error?.responseHeaders),
    source: "opencode:session.error",
  };
}

function permissionRecord(properties) {
  return {
    kind: "permission",
    pane: process.env.HERDR_PANE_ID,
    agent: AGENT,
    request_id: bounded(properties?.id, 64),
    permission: bounded(properties?.permission, 64),
    title: bounded(
      properties?.title ?? (Array.isArray(properties?.patterns) ? properties.patterns[0] : undefined),
      MAX_TITLE,
    ),
    patterns: Array.isArray(properties?.patterns)
      ? properties.patterns.slice(0, MAX_LIST).map((pattern) => bounded(pattern, 120)).filter(Boolean)
      : undefined,
    source: "opencode:permission.asked",
  };
}

function questionRecord(properties) {
  const questions = Array.isArray(properties?.questions) ? properties.questions : [];
  const first = questions[0] ?? {};
  return {
    kind: "question",
    pane: process.env.HERDR_PANE_ID,
    agent: AGENT,
    request_id: bounded(properties?.id, 64),
    title: bounded(first.header ?? first.question, MAX_TITLE),
    question_count: questions.length || undefined,
    source: "opencode:question.asked",
  };
}

function replyRecord(type, properties) {
  return {
    kind: "resolve",
    pane: process.env.HERDR_PANE_ID,
    agent: AGENT,
    // v2 reports requestID; v1 permission.replied reports permissionID.
    request_id: bounded(properties?.requestID ?? properties?.permissionID, 64),
    reply: bounded(properties?.reply ?? properties?.response, 24),
    source: `opencode:${type}`,
  };
}

const HerdrOrchestratorAttentionPlugin = async () => {
  if (
    process.env.HERDR_ENV !== "1" ||
    !process.env.HERDR_SOCKET_PATH ||
    !process.env.HERDR_PANE_ID
  ) {
    return {};
  }

  function publish(record) {
    if (!record || !record.request_id && record.kind === "resolve") {
      return;
    }
    try {
      send(record);
    } catch {
      // A missed notice is recovered by the supervisor's periodic reconcile pass.
    }
  }

  return {
    event: async ({ event }) => {
      const type = event?.type;
      const properties = event?.properties ?? {};
      const sessionID = typeof properties?.sessionID === "string" ? properties.sessionID : undefined;
      const rootID = sessionID ? rootSession(sessionID) : undefined;
      rememberParent(properties);

      switch (type) {
        case "permission.asked":
          publish({ ...permissionRecord(properties), session: rootID });
          break;
        case "question.asked":
          publish({ ...questionRecord(properties), session: rootID });
          break;
        case "permission.replied":
        case "question.replied":
        case "question.rejected":
          publish({ ...replyRecord(type, properties), session: rootID });
          break;
        case "session.error":
          if (rootID) publish({ ...errorRecord(properties), session: rootID });
          break;
        case "session.status": {
          const status = properties?.status;
          const kind = typeof status === "string" ? status : status?.type;
          if (kind === "retry" && rootID) publish({ ...quotaRecord(properties), session: rootID });
          break;
        }
        default:
          break;
      }
    },
  };
};

export default {
  id: "herdr.opencode.orchestrator-attention",
  server: HerdrOrchestratorAttentionPlugin,
  setup() {},
};