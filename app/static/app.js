/*
 * Engine Health API -- frontend behaviour.
 *
 * Everything the UI does lives here: storing the token, deciding which view to
 * show, posting the login form, sending chat turns, and rendering replies.
 * No framework, no build step, no dependencies -- plain ES2020 in one file,
 * loaded by index.html at the end of <body> (so the DOM already exists and no
 * DOMContentLoaded wrapper is needed).
 *
 * The three things in here that are easy to get wrong, all called out again at
 * their implementation:
 *
 *   1. Login is **form-encoded**, not JSON. `/api/auth/login` is parsed by
 *      FastAPI's OAuth2PasswordRequestForm, which reads
 *      application/x-www-form-urlencoded and names the email field `username`.
 *      Posting JSON there returns 422.
 *   2. **Any 401 clears the token and drops back to login.** Tokens expire
 *      (60 minutes by default) and the server may be re-seeded underneath a
 *      logged-in tab, so a stale token has to self-heal rather than leave the
 *      chat view sitting there failing every send.
 *   3. **Model output is never inserted as HTML.** Replies are written with
 *      textContent. The reply text comes from an LLM which is in turn quoting
 *      user-supplied questions, so treating it as markup would be a stored-XSS
 *      path straight through the chat history.
 */

"use strict";

/* ==========================================================================
 * Configuration and module state
 * ========================================================================== */

/**
 * localStorage key holding the JWT.
 *
 * localStorage rather than sessionStorage so a reload or a second tab stays
 * logged in; the server's chat history is per-user and in-memory, so a reload
 * costs nothing but the token. (Not httpOnly-cookie auth: this is a demo
 * talking to a bearer-token API, and swapping to cookies would mean CSRF
 * protection the API does not currently have.)
 */
const TOKEN_KEY = "engine_health_token";

/**
 * Which chat endpoint each role posts to.
 *
 * The two roles are genuinely different endpoints server-side, with different
 * role gates and different response shapes -- the engineer response carries
 * `tool_calls`, the customer response carries `reply` alone. Mapping them here
 * (rather than branching at the call site) keeps that one-to-one relationship
 * in a single readable place.
 */
const CHAT_ENDPOINTS = {
  engineer: "/api/chat/engineer",
  customer: "/api/chat/customer",
};

/**
 * The signed-in user (`{id, email, role}` from /api/auth/me), or null.
 * Set only by `showChat`, cleared only by `showLogin`.
 */
let currentUser = null;

/** True while a chat turn is in flight, to stop double-sends. */
let sending = false;

/* ==========================================================================
 * Element lookups
 * ==========================================================================
 * Resolved once at load. index.html is static, so these can never be missing;
 * caching them avoids re-querying the DOM on every keystroke and every render.
 */

const el = {
  loginView: document.getElementById("login-view"),
  loginForm: document.getElementById("login-form"),
  loginButton: document.getElementById("login-button"),
  loginError: document.getElementById("login-error"),
  email: document.getElementById("email"),
  password: document.getElementById("password"),

  chatView: document.getElementById("chat-view"),
  chatForm: document.getElementById("chat-form"),
  chatInput: document.getElementById("chat-input"),
  sendButton: document.getElementById("send-button"),
  messages: document.getElementById("messages"),
  userEmail: document.getElementById("user-email"),
  userRole: document.getElementById("user-role"),
  logoutButton: document.getElementById("logout-button"),
};

/* ==========================================================================
 * Token storage
 * ==========================================================================
 * Wrapped in try/catch because localStorage throws rather than returning null
 * in a few real configurations -- Safari private browsing historically, and
 * any browser with site data blocked. The app still works for the length of a
 * page load without persistence, which is better than a blank screen.
 */

function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch (err) {
    return null;
  }
}

function setToken(token) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch (err) {
    /* Non-fatal: this session works, the next reload asks for a login. */
  }
}

function clearToken() {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch (err) {
    /* Non-fatal, as above. */
  }
}

/* ==========================================================================
 * HTTP
 * ========================================================================== */

/**
 * Thrown when the server answers 401 on an authenticated request.
 *
 * A distinct error type rather than a status check at every call site: the
 * response to a 401 is always the same (drop the token, return to login), so
 * it is handled once, in the two catch blocks that call `handleUnauthorized`.
 */
class UnauthorizedError extends Error {}

/**
 * Fetch a JSON endpoint with the bearer token attached.
 *
 * @param {string} path      Same-origin path, e.g. "/api/chat/engineer".
 * @param {object} [options] Extra fetch options; `body` is sent as JSON.
 * @returns {Promise<object>} The parsed JSON response body.
 * @throws {UnauthorizedError} On 401 -- the token is stale, forged or expired.
 * @throws {Error} On any other non-2xx, with the server's `detail` if it sent
 *   one (FastAPI's error shape) so the user sees the real reason, e.g. the
 *   403 from a customer calling an engineer-only route.
 */
async function apiFetch(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const token = getToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, {
    ...options,
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  if (response.status === 401) {
    throw new UnauthorizedError("Session expired");
  }

  // Read the body once, then decide -- a failed response usually still carries
  // a JSON `detail`, and calling .json() after .text() on the same response
  // would throw "body already read".
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (err) {
    data = null;
  }

  if (!response.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : null;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return data;
}

/**
 * A 401 landed. Forget the token and go back to login.
 *
 * Called from both the startup check and the chat send path. Never silently
 * retried: if the token is bad, every retry is bad the same way.
 */
function handleUnauthorized(message) {
  clearToken();
  showLogin(message || "Your session has expired. Please sign in again.");
}

/* ==========================================================================
 * View switching
 * ==========================================================================
 * index.html ships with BOTH views hidden. Exactly one of these two functions
 * runs at the end of startup, so the page never flashes the wrong view while
 * /api/auth/me is still in flight.
 */

/** Show the login form, optionally with an error message above it. */
function showLogin(errorMessage) {
  currentUser = null;
  el.chatView.hidden = true;
  el.loginView.hidden = false;
  el.messages.replaceChildren();
  el.password.value = "";

  if (errorMessage) {
    el.loginError.textContent = errorMessage;
    el.loginError.hidden = false;
  } else {
    el.loginError.textContent = "";
    el.loginError.hidden = true;
  }
  el.email.focus();
}

/**
 * Show the chat view for an authenticated user.
 *
 * @param {{id:number, email:string, role:string}} user From /api/auth/me.
 *
 * The role decides the endpoint (see CHAT_ENDPOINTS) and whether the "what I
 * checked" panel is rendered at all. A role with no chat endpoint -- `roles.py`
 * defines `technician` with an empty tool list, and nothing routes it -- gets
 * an explanatory line instead of a chat box that would 403 on every send.
 */
function showChat(user) {
  currentUser = user;
  el.loginView.hidden = true;
  el.chatView.hidden = false;
  el.loginError.hidden = true;

  el.userEmail.textContent = user.email;
  el.userRole.textContent = user.role;
  el.messages.replaceChildren();

  const endpoint = CHAT_ENDPOINTS[user.role];
  if (!endpoint) {
    el.chatForm.hidden = true;
    addSystemMessage(
      `No chat interface exists for the role "${user.role}". ` +
        `Sign in as an engineer or a customer.`
    );
    return;
  }

  el.chatForm.hidden = false;
  addSystemMessage(
    user.role === "engineer"
      ? "Signed in as an engineer. You can ask about any engine in the fleet — " +
          "remaining useful life, anomaly scores, or a full status report. " +
          'Open "What I checked" under a reply to see which tools actually ran.'
      : "Signed in. Ask how your engines are doing and you'll get a plain-language answer."
  );
  el.chatInput.focus();
}

/* ==========================================================================
 * Rendering
 * ==========================================================================
 * Every function here builds nodes and uses textContent. Nothing in this file
 * assigns innerHTML -- see note 3 in the file header.
 */

/**
 * Append a chat bubble.
 *
 * @param {"user"|"assistant"} who Which side of the transcript it sits on.
 * @param {string} text            Plain text; newlines are preserved by CSS.
 * @returns {HTMLElement} The bubble's wrapper, so a caller can attach a tool
 *   panel underneath it or replace it (the "Thinking…" placeholder).
 */
function addMessage(who, text) {
  const row = document.createElement("div");
  row.className = `row row-${who}`;

  const bubble = document.createElement("div");
  bubble.className = `bubble bubble-${who}`;
  bubble.textContent = text;

  row.appendChild(bubble);
  el.messages.appendChild(row);
  scrollToBottom();
  return row;
}

/** Append a centred, non-bubble note: sign-in banner, network errors. */
function addSystemMessage(text) {
  const note = document.createElement("div");
  note.className = "system-note";
  note.textContent = text;
  el.messages.appendChild(note);
  scrollToBottom();
  return note;
}

/** Keep the newest message in view after anything is appended. */
function scrollToBottom() {
  el.messages.scrollTop = el.messages.scrollHeight;
}

/**
 * One human-readable line for a single dispatch result.
 *
 * The shapes come straight from `dispatch_tool_call`: always `tool`,
 * `engine_id`, `engine_label`, `ok`, then `result` on success or `error` on
 * refusal. `engine_label` is null on failure and null for engines nobody owns,
 * so it is appended only when present -- which is exactly what makes a
 * multi-engine answer visibly multi-call rather than one blurred result.
 *
 * @param {object} call One entry from the response's `tool_calls`.
 * @returns {string} e.g. `predict_rul · engine 31 (Engine A) — RUL 6.73 cycles`
 */
function describeToolCall(call) {
  const parts = [call.tool];

  if (call.engine_id === null || call.engine_id === undefined) {
    parts.push("no engine");
  } else if (call.engine_label) {
    parts.push(`engine ${call.engine_id} (${call.engine_label})`);
  } else {
    parts.push(`engine ${call.engine_id}`);
  }

  let outcome;
  if (!call.ok) {
    // Refusals are returned, not raised, so they arrive here as ordinary
    // entries. Showing the server's exact sentence matters: an out-of-scope
    // engine and a nonexistent one deliberately share one wording, and seeing
    // them differ in this panel would be a real finding.
    outcome = `refused — ${call.error || "no reason given"}`;
  } else {
    outcome = summariseResult(call.tool, call.result);
  }

  return `${parts.join(" · ")} — ${outcome}`;
}

/**
 * Condense a tool's result dict into one phrase.
 *
 * Keyed by tool name because the three payloads share no fields:
 * `predict_rul` returns `{predicted_rul}`, `degradation_stage` returns
 * `{predicted_rul, degradation_stage, label}` (where `label` is the *health
 * stage*, not the engine's name), and `anomaly_score` returns
 * `{reconstruction_error, threshold, is_anomaly}`. Anything unrecognised falls
 * back to raw JSON rather than being dropped -- this panel exists to show what
 * happened, so an unknown shape should be visible, not silently blank.
 */
function summariseResult(tool, result) {
  if (!result || typeof result !== "object") {
    return "no result";
  }
  switch (tool) {
    case "predict_rul":
      return `RUL ${result.predicted_rul} cycles`;
    case "degradation_stage":
      return `${result.label} (RUL ${result.predicted_rul} cycles)`;
    case "anomaly_score":
      return (
        `${result.is_anomaly ? "anomaly" : "normal"} ` +
        `(error ${result.reconstruction_error}, threshold ${result.threshold})`
      );
    default:
      return JSON.stringify(result);
  }
}

/**
 * Render the collapsible "what I checked" panel under an engineer reply.
 *
 * Uses a native <details>/<summary>, so collapsing needs no JavaScript and no
 * state to keep in sync.
 *
 * Called for **every** engineer reply, including ones with an empty list, and
 * that is deliberate. `tool_calls` is legitimately empty in two cases that
 * look identical from the outside but are not bugs:
 *   - the model asked a clarifying question instead of running anything;
 *   - the turn was rate-limited ("The assistant is busy..."), which the server
 *     returns as a normal reply with an empty list -- and, per the note in
 *     chat_routes.py, that happens even when the tools already ran.
 * Printing "No tools were run for this reply" is honest about both without
 * inviting either to be mistaken for a broken panel.
 *
 * @param {HTMLElement} afterRow The reply row to insert the panel beneath.
 * @param {Array<object>} toolCalls The response's `tool_calls` array.
 */
function addToolPanel(afterRow, toolCalls) {
  const calls = Array.isArray(toolCalls) ? toolCalls : [];

  const details = document.createElement("details");
  details.className = "tool-panel";

  const summary = document.createElement("summary");
  summary.textContent =
    calls.length === 1 ? "What I checked (1 call)" : `What I checked (${calls.length} calls)`;
  details.appendChild(summary);

  if (calls.length === 0) {
    const empty = document.createElement("p");
    empty.className = "tool-empty";
    empty.textContent =
      "No tools were run for this reply — either the assistant asked a " +
      "clarifying question, or the turn was rate-limited.";
    details.appendChild(empty);
  } else {
    const list = document.createElement("ul");
    for (const call of calls) {
      const item = document.createElement("li");
      item.className = call.ok ? "tool-ok" : "tool-failed";
      item.textContent = describeToolCall(call);
      list.appendChild(item);
    }
    details.appendChild(list);
  }

  afterRow.appendChild(details);
  scrollToBottom();
}

/* ==========================================================================
 * Login
 * ========================================================================== */

/**
 * Exchange email + password for a JWT.
 *
 * **Form-encoded, not JSON** -- see note 1 in the file header. `URLSearchParams`
 * as the body makes the browser set
 * `Content-Type: application/x-www-form-urlencoded` itself, and the email goes
 * in the field named `username` because that is what OAuth2's password flow
 * calls it. This does NOT go through `apiFetch`: that helper JSON-encodes its
 * body and attaches a bearer token, and neither is right here.
 *
 * @returns {Promise<string>} The access token.
 * @throws {Error} With the server's message on a 401 -- one wording for both
 *   an unknown email and a wrong password, on purpose.
 */
async function requestToken(email, password) {
  const body = new URLSearchParams();
  body.set("username", email);
  body.set("password", password);

  const response = await fetch("/api/auth/login", { method: "POST", body });

  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (err) {
    data = null;
  }

  if (!response.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : null;
    throw new Error(detail || `Sign-in failed (${response.status})`);
  }
  return data.access_token;
}

el.loginForm.addEventListener("submit", async (event) => {
  // The browser's default submit would navigate away and lose the page.
  event.preventDefault();

  el.loginError.hidden = true;
  el.loginButton.disabled = true;
  el.loginButton.textContent = "Signing in…";

  try {
    const token = await requestToken(el.email.value.trim(), el.password.value);
    setToken(token);
    // Ask the server who this is rather than decoding the JWT here: the
    // frontend is not the token's audience, and a role changed in the database
    // takes effect immediately this way.
    const user = await apiFetch("/api/auth/me");
    showChat(user);
  } catch (err) {
    // A 401 on /me straight after a successful login means the token was
    // rejected instantly -- treat it like any other sign-in failure rather
    // than bouncing through handleUnauthorized, which would just re-render
    // the form we are already on.
    clearToken();
    el.loginError.textContent =
      err instanceof UnauthorizedError ? "Sign-in failed. Please try again." : err.message;
    el.loginError.hidden = false;
  } finally {
    el.loginButton.disabled = false;
    el.loginButton.textContent = "Sign in";
  }
});

el.logoutButton.addEventListener("click", () => {
  // Client-side only: JWTs are stateless and this app keeps no revocation
  // list, so "log out" means forgetting the token. The server-side chat
  // history for this user id stays in memory until the process restarts.
  clearToken();
  showLogin();
});

/* ==========================================================================
 * Chat
 * ========================================================================== */

el.chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const message = el.chatInput.value.trim();
  // `sending` guards against a second submit while a turn is in flight. The
  // server keeps one history per user and does not lock it, so overlapping
  // turns from the same account can interleave in that history.
  if (!message || sending || !currentUser) {
    return;
  }

  const endpoint = CHAT_ENDPOINTS[currentUser.role];
  if (!endpoint) {
    return;
  }

  el.chatInput.value = "";
  addMessage("user", message);
  setSending(true);

  // A placeholder bubble, replaced in place by the real reply. Chat turns take
  // seconds (two model calls minimum, plus every tool call in between), and
  // without this the UI looks frozen.
  const pending = addMessage("assistant", "Thinking…");
  pending.classList.add("pending");

  try {
    const data = await apiFetch(endpoint, { method: "POST", body: { message } });

    pending.classList.remove("pending");
    pending.querySelector(".bubble").textContent = data.reply;

    // Engineers only. The customer response has no `tool_calls` field at all
    // -- checking the role rather than the field keeps that asymmetry
    // deliberate, so a future server change that started sending the field to
    // customers would not quietly start showing them engine numbers.
    if (currentUser.role === "engineer") {
      addToolPanel(pending, data.tool_calls);
    }
    scrollToBottom();
  } catch (err) {
    pending.remove();
    if (err instanceof UnauthorizedError) {
      handleUnauthorized();
    } else {
      // Everything else -- a 403, a 422, a dropped connection, a restarted
      // server -- is shown as a note rather than a reply bubble, so it is
      // never mistaken for something the assistant said.
      addSystemMessage(err.message);
    }
  } finally {
    setSending(false);
  }
});

/** Lock or unlock the composer while a turn is in flight. */
function setSending(isSending) {
  sending = isSending;
  el.sendButton.disabled = isSending;
  el.chatInput.disabled = isSending;
  el.sendButton.textContent = isSending ? "…" : "Send";
  if (!isSending) {
    el.chatInput.focus();
  }
}

/* ==========================================================================
 * Startup
 * ========================================================================== */

/**
 * Decide which view to show, once, on page load.
 *
 * With no token this is instant. With one, it costs a single request to
 * /api/auth/me -- which doubles as the token's validity check, so an expired
 * token puts the user back on the login form instead of into a chat view where
 * every send would fail.
 */
async function start() {
  if (!getToken()) {
    showLogin();
    return;
  }
  try {
    const user = await apiFetch("/api/auth/me");
    showChat(user);
  } catch (err) {
    if (err instanceof UnauthorizedError) {
      handleUnauthorized();
    } else {
      // The server is down or unreachable. Show the login form with the real
      // reason rather than an empty page -- signing in will fail too, but at
      // least the cause is on screen.
      clearToken();
      showLogin(`Could not reach the server: ${err.message}`);
    }
  }
}

start();
