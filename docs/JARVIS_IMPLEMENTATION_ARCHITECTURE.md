# JARVIS Implementation Architecture

**Status:** Architecture / planning document. Not yet reviewed or approved for implementation.
**Scope:** Maps the approved JARVIS roadmap (SARANA + JARVIS Final Development Roadmap) onto the *actual current codebase*, traced line-by-line where precision matters. No code was written, no files modified, nothing committed or pushed to produce this document.

**Evidence key:** 🟩 confirmed by direct code inspection this session (file/line cited) · 🟨 confirmed from reliable recent session context, not re-read · ❓ inferred, needs a future read before being trusted as fact.

---

## 4. Current System Map

The real pipeline, traced against `main.py`, not assumed from naming:

```
Microphone (real hardware, desktop only)
  │  sd.InputStream callback  — main.py:3607, JarvisLive._listen_audio()
  ▼
out_queue (asyncio.Queue, raw PCM chunks)
  │  main.py:3564, JarvisLive._send_realtime()
  ▼
self.session.send_realtime_input(media=...)  — Gemini Live SDK call
  ▼
Gemini Live (google-genai client.aio.live.connect(), model=LIVE_MODEL)
  │  reasons over audio + conversation + tool declarations, may emit response.tool_call
  ▼
JarvisLive._receive_audio()  — main.py, the `async for response in self.session.receive()` loop
  │  detects response.tool_call.function_calls, pushes them onto self._tool_call_queue
  ▼
Dedicated tool-worker task  — consumes self._tool_call_queue, calls _handle_tool_batch()
  │  executes each function call SEQUENTIALLY, in order — "never uncontrolled parallel
  │  tool execution" (main.py, _handle_tool_batch()'s own docstring) — off the receive
  │  loop, so the conversation stays responsive during a slow tool call
  ▼
JarvisLive._execute_tool(fc)  — main.py:2448
  │  name = fc.name ; args = dict(fc.args or {})
  │  dispatches by name to the real action module (browser_control, computer_control,
  │  computer_settings, office_control, code_helper, dev_agent, ...)
  ▼
Action module (actions/*.py) — does the real work, usually off the event loop via
  loop.run_in_executor(None, lambda: real_function(parameters=args))
  ▼
Result string — plain, or Result-Envelope-tagged ([VERIFIED_SUCCESS]/[INCONCLUSIVE]/...)
  for the modules that share result_envelope.py
  ▼
types.FunctionResponse(id=fc.id, name=name, response={"result": ...})
  │  collected into fn_responses across the whole batch
  ▼
self.session.send_tool_response(function_responses=fn_responses)  — back to Gemini Live
  ▼
Gemini continues reasoning / narrates a spoken reply
  ▼
response.data (raw audio bytes) — sliced into 2400-byte (~50ms) chunks in _receive_audio()
  ▼
audio_in_queue (the SPEAKER-bound queue, despite the name — main.py's own naming)
  ▼
JarvisLive._play_audio()  — main.py, ~3980+
  │  sd.RawOutputStream — main.py:4012
  ▼
Speaker
```

**What this confirms that the earlier Phase-0 audit only inferred:**
- 🟩 There is already a dedicated, ordered, batched tool-execution worker (`_tool_call_queue` → `_handle_tool_batch()`), decoupled from the Gemini receive loop. This is real infrastructure the J4 stage can build on — not something to invent.
- 🟩 There is already a rudimentary per-call state structure: `self._pending_tool_calls` (dict keyed by `fc.id`, carrying a `"status"` field set to `"running"`) and `self._active_tool_task` / `self._active_tool_call_id` / `self._active_tool_name`, used by both cancellation (`cancel_active_task`) and batch bookkeeping.
- 🟩 Barge-in (interruption) and explicit task cancellation are two **deliberately separate** mechanisms — barge-in (`sc.interrupted`) only stops audio and resets the action governor; `cancel_active_task` is the only path allowed to touch `self._active_tool_task` (main.py's own comment, verbatim).

---

## 5. Voice Command → Final Result Trace

### Example 1 — Simple computer task
**"Open Chrome and search for today's Microsoft news."**

| Stage | File / Function | Responsibility |
|---|---|---|
| Speech in | `main.py: JarvisLive._listen_audio()` | Captures mic PCM, queues it |
| Realtime send | `main.py: JarvisLive._send_realtime()` | Streams audio to Gemini Live |
| Reasoning | Gemini Live (external) | Recognizes intent, selects `browser_control` tool, builds args (`browser="chrome"`, query text) |
| Detection | `main.py: JarvisLive._receive_audio()` | Sees `response.tool_call`, enqueues the function call |
| Dispatch | `main.py: JarvisLive._handle_tool_batch()` → `_execute_tool(fc)` (line 2448) | Matches `name == "browser_control"`, calls `browser_control(parameters=args)` off the event loop |
| Execution | `actions/browser_control.py: browser_control()` | Per that module's own documented rule, an ordinary open/search request uses the user's **real, native** browser profile (not the automation session) unless an automation session is already active for that browser |
| Result | Returns a plain string (e.g. `"Opened: <url>"`) | Not currently Result-Envelope-wrapped for a simple open/search (🟨 — matches this project's own documented "fire-and-forget, no real verifiable ground truth" category, same reasoning as `open_system_settings()`) |
| Response | `types.FunctionResponse` → `send_tool_response` | Gemini narrates the result |
| Voice out | `_receive_audio()`'s `response.data` slicing → `audio_in_queue` → `_play_audio()` → `sd.RawOutputStream` | Speaks the confirmation |

### Example 2 — Multi-step technical task (what genuinely works today)
**"Build me a small Python script that renames files in a folder by date, and fix it if it doesn't run."**

This is deliberately chosen over an aspirational "fix my existing app" example — `dev_agent.py`'s real, current scope is building **new** projects from scratch, confirmed by its own tool description in `main.py` ("Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors"), not operating on an existing arbitrary repository.

| Stage | File / Function | Responsibility |
|---|---|---|
| Dispatch | `main.py: _execute_tool()` | `name == "dev_agent"` → `dev_agent(parameters=args)` |
| Planning | `actions/dev_agent.py: _plan_project(description, language)` | One bounded Gemini call (`MODEL_PLANNER`) — produces a file/dependency plan, not a second reasoning loop |
| Writing | `_write_file(...)` per planned file | One bounded Gemini call per file (`MODEL_WRITER`) |
| Dependencies | `_install_dependencies(dependencies, project_dir)` | Real `pip install`/equivalent |
| Run | `_run_project(run_command, project_dir, timeout=30)` | Real subprocess execution |
| Verify | `_has_error(output, run_command)` | Local-signal check — did the run actually fail |
| Recovery | `_classify_error(output)` → `_try_auto_install()` and/or `_fix_files(...)` | Bounded retry loop, `MAX_FIX_ATTEMPTS = 5` — a REAL, already-proven "classify → attempt fix → re-run" pattern |
| Editor handoff | `_open_vscode(project_dir)` | Opens the result for the user |
| Report | `dev_agent()`'s own return string | Summarizes what was built/fixed |

**Architectural note:** `_fix_files`'s bounded classify-fix-reverify loop is exactly the *pattern* J8 (Software Development Agent) should reuse — not the code itself, since it's scoped to a freshly generated project, not an arbitrary existing repository.

### Example 3 — A task that fails and requires recovery
**"Click the Export button" (in an app where the target is genuinely ambiguous — two visually similar controls).**

| Stage | File / Function | Responsibility |
|---|---|---|
| Dispatch | `main.py: _execute_tool()` | `name == "computer_control"`, `action="accomplish"` (or `ui_click`) |
| Discovery | `actions/computer_control.py: accomplish()` → `_ui_find` | Walks the live pywinauto UIA tree for the target |
| Ambiguity | `_pick_best_match` finds more than one plausible control | Returns `[UI_AMBIGUOUS] ...` via `result_envelope.envelope()` — refuses to guess |
| Escalation check | `main.py`, `_cc_ESCALATABLE_TAGS` (built at main.py:112-114 from `computer_control.INCONCLUSIVE_TAGS` + `result_envelope.ESCALATABLE_STATUSES`) | Around main.py:3446-3483, the tool result is checked against this set |
| Vision capture | `main.py: self._pending_vision = (img_b, mime_t, question, "screen")` (main.py:3483) | A screenshot is queued for injection |
| Injection | `main.py`, ~line 3791-3794 | The pending screenshot is injected into the SAME live conversation — a continuation of the existing session, not a second model/reasoning loop |
| Recovery | Gemini looks at the image, either narrows the target and calls `accomplish()`/`ui_click` again with tighter constraints, or asks the user which one they mean | This is the ONE existing recovery mechanism today — escalate to vision, once, within the same turn |

**Gap this confirms for J5 (Recovery):** there is exactly one recovery path today (UIA-ambiguous → vision-in-conversation). There is **no** "try a different control-method tier" logic (e.g., UIA failed → try a keyboard shortcut → try CLI) — J5 is genuinely new work, not an extension of an existing multi-tier fallback, because only one fallback tier currently exists.

---

## 6. Target JARVIS Architecture

Mapping `UNDERSTAND → INSPECT → PLAN → PREPARE → EXECUTE → VERIFY → RECOVER → COMPLETE → REPORT` onto what exists:

| Stage | Meaning | Receives | Produces | Existing code that supports it | New component actually required | Must NOT move here |
|---|---|---|---|---|---|---|
| UNDERSTAND | Turn the user's request into a concrete objective | Raw conversation turn | An objective statement | Gemini Live's own reasoning (no code change needed) | Nothing — this is inherent to the existing tool-calling turn | A second LLM call to "understand" — Gemini Live already does this |
| INSPECT | Learn the current real state before acting | The objective | Active window, UIA snapshot, optionally a screenshot | `computer_control.py`'s `get_active_window_title`, `list_ui_elements`; `screen_processor.py`'s `_capture_screen` | A single composed "current state" query (J6) | Blind action without a prior state read for anything non-trivial |
| PLAN | Decide the ordered steps | Objective + current state | An ordered step list | Gemini's own tool-calling reasoning already sequences multi-step turns via `_handle_tool_batch()`'s ordered execution | Explicit objective/step TRACKING across a turn (J4) — not a new planner | A second planning LLM/agent framework |
| PREPARE | Resolve HOW each step will be done | One step | A chosen control method (API/CLI/DOM/UIA/input/vision) | `system_shortcuts.py` (API/CLI tier), `browser_control.py` (DOM), `computer_control.py` (UIA), pyautogui (input), `screen_find`/`observe` (vision) — every tier already exists | Tool-description language making the preference order explicit (J3) | A rigid "always try tier 1 first" hard-coded loop — let Gemini choose, guided by description text |
| EXECUTE | Run the chosen action | The prepared step | A raw outcome | `_execute_tool()`, the action modules themselves | Nothing structurally new | Verification logic — keep it in the VERIFY stage, not inline |
| VERIFY | Confirm the REAL outcome, not the return code | Raw outcome | A Result Envelope status | `result_envelope.py` — already the shared, correct pattern | Extending Result-Envelope adoption to `code_helper.py`/`dev_agent.py`/a future git module | A second, parallel verification vocabulary |
| RECOVER | Try a better method on failure | A non-success Result Envelope status | Either a retried step (different method) or an honest give-up | The one existing vision-escalation path (Example 3 above) | A bounded, tiered "try the next control method" loop (J5) | Blind same-method retries — explicitly against this project's own already-learned lesson (the live Calculator double-click bug) |
| COMPLETE | Confirm the ORIGINAL objective is met | All step outcomes | A pass/fail against the objective, not the last step | Nothing yet — genuinely new | Objective-level re-check (J4/J10) | — |
| REPORT | Tell the user what happened | Objective outcome | Spoken/text summary | Gemini's own narration, already working today | Nothing new | Silent success/failure — always report honestly, matching this project's own repeated "never claim success you can't back up" principle |

---

## 7. Task State Machine

**Principle: extend the state that already exists (`self._pending_tool_calls[fc.id]["status"]`, `self._active_tool_task`) rather than build a parallel state system.**

```
RECEIVED        — a function call has arrived in a batch (_handle_tool_batch), before _execute_tool runs
UNDERSTANDING   — folded into RECEIVED today (Gemini's own reasoning produced the call); a distinct
                  state only becomes meaningful once J4 tracks a multi-step OBJECTIVE, not just one call
INSPECTING      — new: a state-perception query (J6) is running before the real action
PLANNING        — new: only meaningful for a multi-step objective (J4+), not a single tool call
PREPARING       — new: control-method selection reasoning (J3) — usually instantaneous, may not need
                  to be a visible state at all for single-step calls
EXECUTING       — status:"running" already exists in self._pending_tool_calls
VERIFYING       — folded into the action module's own Result-Envelope construction today; becomes a
                  distinct visible state once multi-step objective tracking (J4) exists
RECOVERING      — new (J5) — only entered on a non-VERIFIED_SUCCESS envelope
COMPLETED       — the function response is sent via send_tool_response; for a multi-step objective (J4+),
                  a second, objective-level COMPLETED check happens after all steps finish
FAILED          — a VERIFIED_FAILURE (or exhausted RECOVERING) envelope reaches send_tool_response
CANCELLED       — already real: cancel_active_task's dispatch (main.py, _execute_tool's own
                  cancel_active_task branch) calls task.cancel() on self._active_tool_task for
                  cancellable (read-only) tools
```

**Rules, kept deliberately simple:**
- A single-step call (today's normal case) only ever needs RECEIVED → EXECUTING → COMPLETED/FAILED/CANCELLED — the richer states only activate for a J4 multi-step objective.
- Verification failure does not automatically mean FAILED — it means RECOVERING, bounded by a small retry budget (J5), then FAILED only if recovery is exhausted.
- Cancellation reuses the exact existing `_active_tool_task`/`_READ_ONLY_TOOLS` mechanism — no new cancellation plumbing.
- Progress reporting: for a multi-step objective, report progress the same way results are already reported today (a spoken/text update per completed step), not a separate UI/progress-bar concept.

---

## 8. File-by-File Implementation Map

### CREATE

```
File:             actions/task_engine.py   (name indicative, not final)
Purpose:          Owns multi-step objective tracking for J4 — the "what is the overall
                  goal, what steps have run, did we actually achieve it" state that
                  does not exist anywhere today.
Why necessary:    self._pending_tool_calls tracks individual CALLS, not an OBJECTIVE
                  spanning several calls. Nothing currently answers "did the original
                  goal succeed," only "did the last action succeed."
Responsibilities: Objective/step record-keeping; end-of-objective verification against
                  the ORIGINAL goal; feeding progress back to JarvisLive for narration.
Must NOT do:      Run its own LLM reasoning loop. Choose control methods (that's J3's
                  tool-description work, resolved by Gemini itself). Duplicate
                  result_envelope.py's status vocabulary — it CONSUMES envelopes,
                  it doesn't invent a new status set.
Dependencies:     result_envelope.py (for step-level statuses), main.py's existing
                  _pending_tool_calls/_tool_call_queue plumbing.
```

```
File:             actions/git_control.py   (name indicative)
Purpose:          J9 — the one universally missing primitive; no git wrapper exists
                  anywhere in the codebase today (confirmed by search: zero matches
                  for git subprocess calls in actions/ or main.py).
Responsibilities: status/diff/add/commit (SAFE once J8's test-run verification passes),
                  branch create/switch (SAFE), push (CONFIRMATION REQUIRED, always).
Must NOT do:      Force-push, history rewrite, or any destructive git operation —
                  BLOCKED BY DEFAULT, permanently, per the roadmap's own safety
                  evolution table.
Dependencies:     result_envelope.py, J8's verification output (to gate commit SAFE
                  status on tests actually passing).
```

```
File:             actions/repo_agent.py   (name indicative — J8's real new work)
Purpose:          Repo-wide code search/inspection and EXISTING-project test-run
                  integration — the actual gap identified in § 5's tracing:
                  code_helper.py is single-file scoped, dev_agent.py is
                  new-project-from-scratch scoped, and NEITHER searches or operates
                  on an arbitrary existing repository.
Responsibilities: Search across a real repo's files; run that repo's OWN test command
                  (not a generated project's); apply the classify-fix-reverify PATTERN
                  proven in dev_agent.py's _fix_files, adapted to an existing codebase.
Must NOT do:      Reimplement code_helper.py's single-file edit/explain functions —
                  REUSE those once a target file is identified by this module's own
                  search step.
Dependencies:     code_helper.py (file-level edit primitives), a future git_control.py
                  (J9) for the eventual commit step, result_envelope.py.
```

### MODIFY

```
File:             main.py
Current:          Owns the whole tool-dispatch/session lifecycle; _pending_tool_calls
                  already tracks per-call status.
Why it changes:   J4 needs a place to record which multi-step OBJECTIVE a batch of
                  tool calls belongs to, and to run the end-of-objective verification
                  check before reporting completion.
After change:     Same responsibilities, PLUS: routes a detected multi-step objective
                  through the new task_engine.py rather than treating every tool call
                  as independent.
Unchanged:        _tool_call_queue/_handle_tool_batch's ordering guarantee, barge-in
                  handling, cancellation semantics, the audio pipeline — none of these
                  need to change for J4.
Dependencies:     task_engine.py (new).
Risk:             Medium — this is the single most-depended-upon file in the whole
                  system; changes here need the most conservative, incremental
                  approach of any file in this plan.
```

```
File:             actions/computer_control.py
Current:          Single-step accomplish()/ui_find/ui_click with ONE recovery path
                  (vision escalation).
Why it changes:   J5 needs a bounded, tiered "try the next control method" recovery
                  loop; J6 needs a composed pre-action state query.
After change:     Same accomplish()/ui_find/ui_click contract UNCHANGED for existing
                  callers; ADDS a recovery wrapper and a state-query function.
Unchanged:        The no-blind-retry policy, Result Envelope usage, UIA discovery
                  logic itself.
Dependencies:     result_envelope.py.
Risk:             Low-medium — additive, not a rewrite of the proven core.
```

```
File:             actions/code_helper.py
Current:          Single-file write/edit/explain/run/optimize/screen-debug.
Why it changes:   J8's repo_agent.py needs to CALL these once a target file is found —
                  today nothing outside Gemini's own tool selection invokes them
                  programmatically.
After change:     Its existing functions become callable primitives from repo_agent.py,
                  in addition to remaining directly callable as today's `code_helper`
                  tool.
Unchanged:        Everything about its own single-file scope and behavior.
Dependencies:     None new.
Risk:             Low — purely additive reuse, no behavior change to the existing tool.
```

```
File:             main.py's TOOL_DECLARATIONS / DESKTOP_ONLY_TOOLS
Current:          38 tools, JARVIS-mode gating already present for the higher-autonomy
                  computer_control actions.
Why it changes:   New tools (task_engine-driven multi-step entry point if exposed
                  separately, git_control, repo_agent) need declarations and gating.
After change:     Same gating MECHANISM (DESKTOP_ONLY_TOOLS, JARVIS-mode checks) reused
                  for every new tool — no new gating mechanism invented.
Unchanged:        The gating logic itself.
Dependencies:     result_envelope.is_consequential (git push, any destructive op).
Risk:             Low if the existing pattern is followed exactly; medium if a new
                  parallel gating idea is introduced instead.
```

### REMOVE

*No file is recommended for removal at this stage.* Both `code_helper.py` and `dev_agent.py` remain — they solve real, different, currently-working problems (single-file work; from-scratch project generation) that repo_agent.py does not replace. `open_app.py`/`send_message.py` were already flagged (prior roadmap) as migration targets onto a general controller, not deletion targets — their removal, if any, only follows after that migration is proven safe, and is out of scope for this document.

### KEEP

```
File:             actions/result_envelope.py
Why it stays:     The one shared verification/safety vocabulary — every module in this
                  entire plan (existing and new) is designed to extend its use, never
                  to duplicate it.
Owns:             STATUS_* constants, envelope(), is_consequential(), is_confirmed().
Future devs must
NOT duplicate:    A second status vocabulary, a second consequential-action classifier,
                  or per-module ad-hoc "success"/"failure" strings for anything with a
                  real verifiable outcome.
```

```
File:             actions/browser_control.py
Why it stays:     Proven, tested, real session-reuse architecture (_SessionRegistry/
                  _BrowserSession) — exactly the shape JARVIS's other controllers
                  should be modeled after, not replaced.
Owns:             All real browser session lifecycle, DOM-based smart_click/smart_type.
Future devs must
NOT duplicate:    A second way to open/track a browser session (this was the literal
                  root cause of the YouTube reuse bug fixed earlier this project).
```

```
File:             main.py's _tool_call_queue / _handle_tool_batch / _pending_tool_calls
Why it stays:     Already-correct sequential, ordered, off-the-receive-loop tool
                  execution — the exact substrate J4 needs, confirmed by this
                  session's own direct reading, not assumed.
Owns:             Tool-call batching, ordering, and per-call status tracking.
Future devs must
NOT duplicate:    A second tool-execution loop or a second per-call state dict.
```

---

## 9. Single Source of Truth

| Responsibility | Single owner | Existing/New | Notes |
|---|---|---|---|
| Voice/audio | `main.py: JarvisLive._listen_audio/_send_realtime/_receive_audio/_play_audio` | Existing | No change needed for any JARVIS stage |
| Gemini Live session | `main.py: JarvisLive` (the whole class) | Existing | Shared by SARANA and JARVIS modes alike |
| Task/objective understanding | Gemini Live's own reasoning (no code) | Existing | Not a code responsibility to own |
| Per-call task state | `main.py: self._pending_tool_calls`, `_active_tool_task` | Existing, to extend | J4 extends this to objective-level, doesn't replace it |
| Multi-step objective state | *(none today)* | **New** — `actions/task_engine.py` | The one genuinely new state-owning module |
| Planning | Gemini's own tool-call sequencing (ordering already handled by `_handle_tool_batch`) | Existing | J4 formalizes tracking, doesn't add a planner |
| Tool/method selection | Gemini, guided by tool descriptions in `main.py` | Existing | J3 sharpens the guidance, doesn't add a selector module |
| Computer control (UIA) | `actions/computer_control.py` | Existing | Do not fork a second UIA layer |
| Browser control | `actions/browser_control.py` | Existing | Do not fork a second browser layer |
| System fast-paths (CLI/API) | `actions/system_shortcuts.py`, `actions/computer_settings.py` | Existing | |
| Office documents | `actions/office_control.py` | Existing | |
| UI inspection | `actions/computer_control.py: list_ui_elements` | Existing | |
| Safety classification | `actions/result_envelope.py: is_consequential/is_confirmed` | Existing | ALL new consequential actions (git push, deploy, delete) register here |
| Verification | `actions/result_envelope.py` (status vocabulary) + each module's own readback logic | Existing | New modules (git_control, repo_agent) must adopt, not reinvent |
| Recovery | *(one path exists: vision escalation)* | Existing partial, **New**: tiered method-fallback (J5) | |
| Memory | `memory/memory_manager.py`, `memory_cache.py`, `postgres_repo.py` | Existing | Shared by SARANA/JARVIS; task-memory namespace is new (roadmap § 12) |
| Documentation/knowledge lookup | `actions/web_search.py` (existing, general) | Existing, to extend (§ 14) | No new search infra needed — usage discipline is the gap |
| Reporting | Gemini's own narration + `_execute_tool`'s return string | Existing | |
| Single-file code work | `actions/code_helper.py` | Existing | |
| New-project generation | `actions/dev_agent.py` | Existing | |
| Existing-repo code work | *(none today)* | **New** — `actions/repo_agent.py` | |
| Git operations | *(none today)* | **New** — `actions/git_control.py` | |

No duplication was found among currently-EXISTING owners — the "multiple owners for one responsibility" case does not currently apply. The three New rows above are the only genuinely missing single-owner responsibilities.

---

## 10. Universal Control Hierarchy

| Layer | Exists today as | Notes |
|---|---|---|
| Reliable API | `actions/office_control.py` (win32com), `actions/computer_settings.py` (pycaw) | |
| CLI / PowerShell | `actions/system_shortcuts.py`'s cmdlet registry | Data-driven, easy to extend (J7) |
| Browser DOM / accessibility | `actions/browser_control.py`'s `smart_click`/`smart_type` | |
| Windows UI Automation | `actions/computer_control.py`'s `accomplish()`/`_ui_find` | |
| Keyboard / mouse | `actions/computer_settings.py` (type_text/press_key), pyautogui direct calls | |
| Gesture | `actions/gesture_control.py` (move/click/drag/scroll — confirmed more complete than earlier assumed) | Not currently part of the JARVIS control-method hierarchy Gemini reasons over — it's a separate, always-on input mode, not a fallback tier |
| Vision / screen interaction | `actions/computer_control.py`'s `screen_find`/`screen_click`, `observe`/`verify` | The one existing recovery/fallback mechanism |
| Ask user / report limitation | Every Result-Envelope `INCONCLUSIVE`/`UI_AMBIGUOUS`/`CONFIRMATION_REQUIRED` path | Already the correct final fallback |

**How JARVIS should choose (J3):** every layer above already exists as a real, callable capability. The gap is not a missing layer — it's that the CHOICE between them today depends on which tool Gemini happens to reach for, not an explicit stated preference order. J3's actual work is tool-description language (the same technique already used to fix the YouTube tool-continuity bug), not a new "method selector" abstraction. **Do not build a naming/dispatch abstraction for this** — it would duplicate what `_execute_tool()`'s existing `name`-based dispatch already does.

---

## 10a. Capability Families *(added post Phase 2 review, before Phase 3 — a correction to the original Phase 3 proposal, not part of the original roadmap text above)*

The Control Hierarchy (§10) is about *how* one capability gets executed (API vs. CLI vs. UIA vs. vision). Capability **families** are a separate, orthogonal concept: *what kind of thing* a capability is, for two specific purposes only — classification, and bounding RECOVERY. A family is **not** a task-sequencing boundary: one objective's PLAN/EXECUTE steps may legitimately cross families in order (`system → application → files` for "open Excel and save this to a specific folder" is ordinary multi-step sequencing, untouched by this).

```
CAPABILITY FAMILIES
├── SYSTEM        — universal OS-level infrastructure (audio, display, windows,
│                    shortcuts, OS settings). Not yet populated — Phase 3.
├── APPLICATION   — capabilities tied to one specific application/domain
│                    (browser, YouTube, Office, VS Code/dev, ...).
│                    youtube/browser live here today (task_engine.py's
│                    _DOMAINS); Office joins this SAME family in Phase 4.
├── RESOURCE      — files / terminal / processes. Concept only, not built.
├── DEVELOPMENT   — repo agent / git. Concept only, not built.
└── DEPLOYMENT    — deploy + verify. Concept only, not built.
```

Only SYSTEM and APPLICATION are real, implemented families right now (`actions/task_engine.py`'s `FAMILY_SYSTEM`/`FAMILY_APPLICATION` constants, and a `family` key on every `_DOMAINS` entry). RESOURCE/DEVELOPMENT/DEPLOYMENT are documented placeholders for where those future J7–J11 capabilities will register once built — no empty scaffolding exists for them yet, deliberately.

**Why this exists:** the original Phase 3 proposal bundled System and Office capabilities together for reasons that turned out to be effort/convenience ("these are the next Result-Envelope-ready modules"), not a real architectural judgment that they belong together — they don't. Office is an Application/Domain capability, the same category as Browser and YouTube; System capabilities (volume, display, OS settings) are a different, universal-infrastructure category. Left uncorrected, the router's flat `_DOMAINS` list had no way to prevent System and Application capabilities from collapsing into one undifferentiated category as more domains were added.

**What families actually enforce:** `execute_task()`'s recovery hop (`_RECOVERY_CHAIN`) only proceeds when `family_of(next_domain) == family_of(current_domain)` — a capability may fail over to a *different method within its own category*, never to an unrelated category. A failed volume-set falling back to a browser search would not be a sane recovery of anything; the family check makes that structurally impossible rather than relying on the chain being hand-curated correctly forever.

**Corrected Phase 3/4 split:**
- **Phase 3 — System capabilities.** `computer_settings.py`/`system_shortcuts.py` only, `family: "system"`. One family, cleanly scoped.
- **Phase 4 — Application/Domain capabilities, second pair.** `office_control.py` added as a *third* entry in the same family youtube/browser already occupy (`family: "application"`) — a genuine test of the router handling three domains in one family, not a System/Domain conflation.

### Phase 3 — implemented (SYSTEM family populated)

Three domains — deliberately not one giant `"system"` bucket, and not one domain per underlying action either (`computer_settings.py`'s ACTION_MAP alone has ~50 entries; enumerating each as its own `_DOMAINS` entry would be exactly the over-fragmentation the correction warned against):

| Domain | Reuses | Notes |
|---|---|---|
| `system_volume` | `computer_settings(action="volume_set", value=N)` | N is extracted from the objective by a plain regex — JARVIS's own deterministic parsing, never a second LLM call. Already Result-Envelope-verified (set-then-readback) by the module it calls. |
| `system_power` | `computer_settings(action="sleep"/"restart"/"shutdown", confirmed=...)` | Grouped as one domain because they share ONE safety story (the existing `is_consequential()` gate), not because they're keyword-similar. `confirmed` is threaded from `jarvis_task`'s own parameter straight through — the gate itself is reused exactly as-is, never reimplemented or relaxed. |
| `system_shortcut` | `system_shortcuts.system_shortcut(objective)` | The entire 40-pane/11-query registry as ONE domain — task_engine does not re-implement per-shortcut matching, it hands the raw objective to that module's own already-proven deterministic resolver. |

**Recovery:** no natural SYSTEM→SYSTEM production recovery pair was found among these three domains (no genuinely equivalent "alternative method" exists for any of them at the Task Engine level — `volume_set`'s own internal keypress fallback already lives inside `computer_settings.py` itself, transparent to the Task Engine). `_RECOVERY_CHAIN` gained zero new entries in Phase 3. The family-scoped mechanism itself (SYSTEM→SYSTEM permitted, SYSTEM↔APPLICATION rejected in both directions) is proven via a deliberately fabricated test fixture instead — the same honest technique already used for the Phase 2.5 cross-family test, not a claim about real chain contents.

**A real, pre-existing dispatch bug found and fixed while integrating this (not introduced by it):** `main.py`'s `computer_settings` branch in `_execute_tool()` was missing its own `result =` assignment, silently falling through to the method's `result = "Done."` default regardless of what `computer_settings()` actually returned — including `CONFIRMATION_REQUIRED`/failure envelopes. Confirmed both statically and empirically (a mocked non-`"Done."` return value was silently discarded, no exception raised) before fixing. Fixed alongside the JARVIS-mode boundary redirect for the three migrated actions.

**Terminal-status correctness fix, exercised for the first time by this phase:** `execute_task()`'s recovery loop previously treated any non-`VERIFIED_SUCCESS` status as potentially recoverable, including `CONFIRMATION_REQUIRED` and `BLOCKED` — never actually exercised in Phase 2 (youtube/browser never return those statuses). Phase 3's `system_power` made this reachable for the first time, and it was wrong: a different domain can't supply a human's "yes," and can't turn "blocked by policy" into "allowed." Both are now explicitly terminal in `execute_task()`, returned immediately like `VERIFIED_SUCCESS`, never checked against `_RECOVERY_CHAIN`.

### Phase 4 — implemented (Office joins APPLICATION)

`office` is a third `_DOMAINS` entry with `family: "application"`, alongside `youtube`/`browser` — reuses `actions/office_control.py` exactly as it already exists (Word: `insert_text`/`replace_text`/`format_selection`/`save`; Excel: `set_cell`/`get_cell`/`save`), no second Office controller.

**Objective parsing:** `_parse_office_action()` deterministically maps an objective to `office_control()`'s own `(app, action, ...)` parameter shape — same "JARVIS's own extraction, never a second LLM call" technique as `_run_system_volume`'s numeric parsing. Word's three content actions are Word-only in `office_control.py`, so the action verb itself (`replace`/`bold`·`italic`·`underline`/`insert`·`write`·`type`) determines the app; Excel's two actions need a cell reference to determine both. A bare "open Word"/"open Excel" with no content instruction returns `None` — `office_control.py` has no generic "just open the app" action, and `_run_office()` reports that honestly (`INCONCLUSIVE`) rather than fabricating one.

**Routing declaration order:** `office` is declared *before* `browser` in `_DOMAINS` specifically because "open Word"/"open Excel" tie 1-1 against `browser`'s own `open` keyword (`route()` breaks ties by earliest declaration, the same mechanism that already lets `youtube` beat `browser`) — verified empirically, not assumed (`tests/test_task_engine_office.py`'s collision tests).

**A disclosed, not fixed, capability gap:** `office_control.py` has no PowerPoint support at all (Word/Excel only). The `office` domain deliberately does not claim a `powerpoint` keyword, so "open PowerPoint" is not misrouted into a capability that doesn't exist — it falls to `browser` instead. Adding real PowerPoint support would mean extending `office_control.py` itself, outside this integration phase's scope.

**Recovery:** no `office` entry was added to `_RECOVERY_CHAIN` — `office_control.py` exposes no genuine alternative-method relationship the way `youtube`→`browser` does; a failed `insert_text`/`set_cell` falling back to browser or a system domain would not be a sane recovery of anything. Same no-artificial-recovery discipline as Phase 3.

**Gemini/JARVIS boundary:** `main.py`'s `office_control` branch redirects the specific `(app, action)` pairs the `office` domain covers — which is, deliberately, nearly `office_control.py`'s entire real surface (unlike `computer_settings.py`'s ~50-action/5-migrated split), listed as explicit tuples so a future unmigrated action (e.g. real PowerPoint support, if ever added) stays directly callable rather than silently disappearing behind the redirect.

### Phase 5A — implemented (multi-objective sequencing, structural foundation)

`jarvis_task` accepts an OPTIONAL `objectives: list[str]` alongside the existing `objective: str` — Gemini's own decomposition of a genuinely compound request into ordered, atomic, still-plain-language sub-objectives (never a domain/tool name; the same non-negotiable rule as a single objective, just applied per item). A single `objective` call is unaffected — a one-item plan behaves byte-for-byte as it always has.

**Gemini decomposes intent; JARVIS builds the executable plan.** `build_plan()` runs the EXISTING `route()` once per incoming objective, up front, before any of them execute, producing a `list[PlanStep]` — JARVIS's own artifact (`objective` + JARVIS's routing decision + status). Gemini's `objectives` list has no field for a domain name; `build_plan()`'s own signature takes only objective strings. If any objective fails to route, the WHOLE plan is rejected before anything runs — no partial execution of a compound task JARVIS already knows it can't fully carry out.

**Sequencing vs. recovery, kept structurally disjoint:** `execute_task()`'s new outer loop advances `current_step_index` through the plan only on a PlanStep reaching `VERIFIED_SUCCESS` — sequencing may cross families freely (SYSTEM→APPLICATION is ordinary). The former whole body of `execute_task()` is now `_execute_step()`, called once per PlanStep, completely unchanged in its own logic: family-scoped recovery still only hops within one PlanStep's attempt, never advances the plan. The one real change `_execute_step()` needed: its attempt budget is now a local `attempts` counter rather than `len(task.steps)`, since `task.steps` is shared across every PlanStep in a multi-objective Task now — the old length check would have silently shrunk later PlanSteps' own recovery budget.

**Failure is terminal for the whole task, not just one step:** the first PlanStep that doesn't reach `VERIFIED_SUCCESS` (failed, blocked, or awaiting confirmation) stops the task there — later PlanSteps are never attempted, already-completed ones are never rerun, and JARVIS never modifies or replans the remaining PlanSteps. The only adaptivity is the pre-existing, family-scoped, same-step recovery mechanism.

**TaskContext — small, structured, runtime-only, opt-in:** `values: dict[str, str]` holds a few deterministically-extracted scalars a later objective may consume; `raw: dict[int, str]` keeps each completed PlanStep's own evidence string as an audit trail. `_extract_context_values()` populates `values` after a `VERIFIED_SUCCESS` using the same local-regex discipline as `_parse_office_action` (today's one rule: `system_shortcut`'s battery-percent evidence → `values["percent"]`). Every handler gained a third, mostly-ignored `context` parameter (same uniform-signature pattern Phase 3 used for `confirmed`); `_run_office`/`_parse_office_action` is the one Phase 5A consumer — it falls back to `context.values` for an Excel `set_cell` ONLY when the objective's own text contains a referential word ("that"/"it") and has no literal value of its own, never a silent substitution. `TaskContext` lives on one `Task` instance only — a fresh task never sees a prior task's values.

**Task-level `VERIFIED_SUCCESS` requires every PlanStep to have independently verified success** — dispatching every step is not the same as the task succeeding.

**Proof workflow (fabricated in tests, not yet a production capability):** *"check my battery percentage" → "put that percentage into cell A1"* exercises real cross-family sequencing and real context flow end-to-end, through the actual `main.py` dispatch, with only the underlying `computer_settings`/`office_control` calls mocked — see `tests/test_task_engine_phase5a.py`. Phase 5B is what would make this an actual approved user-facing workflow; Phase 5A only proves the mechanism.

### Phase 5B — implemented (first real compound workflow, frozen)

The Phase 5A mechanism, unchanged in code, now backs an approved, Gemini-reachable compound objective: *"Check the battery percentage."* → *"Put that percentage into cell A1."* — SYSTEM (`system_shortcut`'s battery query) → APPLICATION (`office`'s `set_cell`), proven both with mocked capabilities (`tests/test_task_engine_phase5b.py`) and with a genuine, non-mocked, end-to-end run through the real `main.py` dispatch (a real battery read, a real `TaskContext` handoff, a real Excel COM write, a real cell readback) — see the Phase 5B report for the exact transcript.

**A real, disclosed precision requirement found while wiring this up:** the illustrative phrasing *"put it into Excel"* (no cell named) is genuinely NOT executable — `office_control.py` has no way to know WHERE to write, and `_parse_office_action()` correctly returns `None` → honest `INCONCLUSIVE` rather than guessing a cell. This isn't a bug; it's why `jarvis_task`'s tool description was updated (the only code change Phase 5B needed outside tests) to tell Gemini explicitly that a spreadsheet objective must name a concrete cell — the same "clarify ambiguity into a self-contained sentence" job Gemini already does for pronouns, extended to this one concrete case. Nothing in `task_engine.py`, `office_control.py`, or the family/recovery/verification model changed.

---

## 11. Plan / Action / Verification Model

Minimal fields, no more than needed:

```
Step
├── objective        — the ORIGINAL user goal this step serves (for J4's end-of-objective check)
├── action            — tool name + args (what _execute_tool already receives)
├── method            — which control-hierarchy tier was used (for J3/J5's benefit, logging only)
├── expected_result    — what "success" looks like, in checkable terms
├── actual_result       — the raw outcome from the action module
├── verification        — the Result Envelope status (existing vocabulary, not new)
└── recovery_attempt    — None, or a record of which fallback tier was tried next (J5)
```

`required_context` and `failure_reason` from the brief's own suggested shape are deliberately folded into `actual_result`/`verification` rather than kept as separate fields — Result Envelope's `evidence` string already carries that information; a separate field would duplicate it.

---

## 12. Verification

**Already correctly designed, needs wider adoption, not a redesign.**

| Action | Today's verification | Owner |
|---|---|---|
| `computer_control.py` clicks/types | Before/after UIA state diff | Existing |
| `computer_settings.py` volume/wifi/bluetooth/audio | Set-then-read-back | Existing |
| `office_control.py` Word/Excel writes | Read the same cell/selection back | Existing |
| `system_shortcuts.py` queries | The command's own real output IS the evidence | Existing |
| `dev_agent.py` project builds | `_has_error()` on the real run output | Existing, not Result-Envelope-wrapped ❓ |
| `code_helper.py` runs | `_has_error()` on run output | Existing, not Result-Envelope-wrapped ❓ |
| Git commit (J9, new) | Confirm the commit hash actually exists in `git log` afterward, not just that the command returned 0 | New |
| Test run (J8, new) | Inspect the ACTUAL test-runner output for pass/fail per test, not just process exit code | New |
| Browser action (existing, extend) | `browser_control.py`'s own DOM-state checks where present; extend to more actions | Existing partial |
| Deployment (J11, new) | Independent live HTTP check against the deployed URL, exactly as this project's own development sessions already do manually | New |

The one concrete gap this section surfaces: `code_helper.py` and `dev_agent.py`'s `_has_error()` checks are real and local-signal-based (good), but neither currently returns a `result_envelope.py`-tagged string — they predate that shared vocabulary. Bringing them onto it is low-risk, additive work, not a rewrite.

---

## 13. Recovery

```
FAILURE (a non-VERIFIED_SUCCESS Result Envelope status)
  ↓
UNDERSTAND FAILURE  — read the envelope's evidence string (already descriptive by design)
  ↓
CLASSIFY FAILURE    — was it UI_AMBIGUOUS / INCONCLUSIVE / VERIFIED_FAILURE?
  ↓                    (VERIFIED_FAILURE is a KNOWN real outcome — do not retry it blindly,
  ↓                     matches result_envelope.py's own existing ESCALATABLE_STATUSES design)
TRY BETTER METHOD    — only for UI_AMBIGUOUS/INCONCLUSIVE: step down the control hierarchy
  ↓                     (§ 10) — e.g. UIA ambiguous → vision (today's only real example);
  ↓                     future: CLI unavailable → UIA, API unavailable → CLI
VERIFY               — the SAME verification the original step would have used
```

**Rules, matching the roadmap's own commitments:**
- Bounded recovery budget — reuse `_JARVIS_MAX_ACTIONS_PER_TURN` (main.py:1325, already 20) as the outer bound; a recovery attempt still counts against it, it doesn't get a separate unlimited budget.
- Never retry the exact same method that just failed — this is already the explicit, hard-learned policy (the Calculator double-click bug); J5 extends it to "try a DIFFERENT tier," never relaxes it to "try again."
- Record what failed — feeds `task_engine.py`'s step record (§ 11).
- Escalate to the user when every tier is exhausted — the existing `CONFIRMATION_REQUIRED`/honest-failure envelope pattern already does this correctly; J5 doesn't need a new "ask the user" mechanism.

---

## 14. Documentation / Knowledge Lookup

```
Does JARVIS already know the reliable method?
  ↓ YES → proceed directly (the common case — most of § 10's hierarchy is already
  ↓        known, hard-coded-by-design capability, not something to look up per call)
  ↓ NO / unfamiliar / version-sensitive
  ↓
Find authoritative documentation — actions/web_search.py (existing, general-purpose)
  ↓
Understand the required API/tool/process — a bounded Gemini call over the fetched
  content, same pattern already used in system_shortcuts.py's registry research
  and file_processor.py's outline generation
  ↓
Execute → Verify (unchanged from § 12)
```

**No new lookup infrastructure is needed.** `web_search.py` already exists and is general-purpose; the gap (if any) is a usage-discipline one — knowing WHEN to look something up (version-sensitive APIs, unfamiliar CLI flags) versus when JARVIS already reliably knows the method — which is prompt/tool-description work, not new code, consistent with how the control-hierarchy preference (J3) is also resolved through description language rather than new abstraction.

---

## 15. Open-Source Research

| Project | Repository | License | Problem it solves | What JARVIS can learn | Reusable code | Verdict | Reason |
|---|---|---|---|---|---|---|---|
| Microsoft UFO² | [github.com/microsoft/UFO](https://github.com/microsoft/UFO) | MIT | Hybrid UIA + visual-grounding "Desktop AgentOS" for Windows | Validates the UIA-first, vision-as-enhancement direction this project already took independently | None directly reusable — full agent framework, different session/tool-calling shape than Gemini Live | **STUDY ONLY** | Confirms the architecture, doesn't need adopting — this project's own tiered control hierarchy already matches its conclusions |
| OSWorld / OSWorld 2.0 | [github.com/xlang-ai/OSWorld](https://github.com/xlang-ai/osworld) | Research/benchmark license (verify before any code reuse) | A real-VM benchmark (369+ tasks) for evaluating computer-use agents with execution-based verification scripts | The EVALUATION METHODOLOGY — verify against real resulting OS state via a scripted check, exactly `result_envelope.py`'s own philosophy | None — it's Linux/VM-centric, not a Windows-native library | **STUDY ONLY** | Validates § 19's testing philosophy; not directly integrable (different OS/VM model than this desktop app) |
| ReAct (Yao et al., ICLR 2023) | [github.com/ysymyth/ReAct](https://github.com/ysymyth/ReAct) | MIT (paper code) | A PROMPTING PATTERN, not a library — interleave reasoning ("Thought") with tool calls ("Action") and results ("Observation") | J4's target loop is already structurally a ReAct-style loop (Gemini reasons → calls a tool → gets a result → continues) — the paper is a naming/observability reference, not code to install | None — there is no framework here to depend on | **USE the pattern, no dependency** | Improves how J4's step records are logged/reasoned about; adds zero new dependencies |
| browser-use | [github.com/browser-use/browser-use](https://github.com/browser-use/browser-use) | MIT | DOM/accessibility-tree extraction for LLM-driven browser automation | Its DOM-extraction technique is a plausible upgrade to `browser_control.py`'s existing `smart_click`/`smart_type` | Its DOM-service technique specifically — as a LIBRARY import, not its own Agent orchestrator | **ADAPT the technique only** | Already the conclusion from the earlier deep investigation this session; re-confirmed, not re-researched |

**Do not add another agent framework.** Every project above either validates the existing architecture (UFO², OSWorld) or offers a pattern/technique to adapt (ReAct's structure, browser-use's DOM extraction) — none of them replace or wrap `JarvisLive`.

---

## 16. Implementation Roadmap (J0–J11)

Stage numbering adjusted from the previously approved J1–J11 by inserting **J0 — Core Task Lifecycle** as this document's own required first stage (the brief explicitly lists it), and reordering J2/J3 relative to the earlier roadmap only where this session's code tracing justifies it (explained inline).

### J0 — Core Task Lifecycle
- **Goal:** Land `task_engine.py` and the state-machine extension (§ 7/§ 8) with ZERO behavior change for any existing single-step tool call.
- **User-visible capability after completion:** None yet, deliberately — this is the one pure-infrastructure stage in the whole plan, kept intentionally small and separate so it can be verified in isolation before anything depends on it.
- **Existing components reused:** `main.py`'s `_tool_call_queue`/`_handle_tool_batch`/`_pending_tool_calls` (extended, not replaced).
- **Files CREATE:** `actions/task_engine.py`.
- **Files MODIFY:** `main.py` (minimal — wire objective-tracking as an opt-in path, existing single-step calls untouched).
- **Files REMOVE:** none.
- **Files KEEP:** everything in § 8's KEEP list.
- **Dependencies:** none — first stage.
- **Implementation order:** state model → task_engine.py → main.py wiring, in that order.
- **Tests:** unit tests on `task_engine.py` in isolation (no real tool calls); a regression pass confirming every existing single-step tool call is byte-for-byte unaffected.
- **Verification:** existing test suite (`test_accomplish.py`, `test_computer_control.py`, etc.) passes unchanged.
- **Definition of done:** a multi-step objective CAN be tracked, but nothing currently uses that path yet.
- **Unlocks next:** J4 (which is the stage that actually starts USING this).

### J1 — Task State *(folded into J0 above — the brief's J1 "Task State" and J0 "Core Task Lifecycle" describe the same infrastructure; splitting them would create two files owning one responsibility, violating § 9's own single-source-of-truth rule)*

### J2 — Universal Actions
- **Goal:** Migrate `open_app.py`/`send_message.py` onto the general controller; formalize the `ComputerController` facade named in the earlier roadmap.
- **User-visible capability:** Every simple app-launch/message action becomes as reliable (Result-Envelope-verified) as `youtube_video.py`'s already-fixed browser reuse.
- **Existing components reused:** `computer_control.py`'s tiers, `result_envelope.py`.
- **Files MODIFY:** `open_app.py`, `send_message.py`.
- **Files CREATE:** possibly a thin facade module, TBD at implementation time — do not pre-invent a filename here.
- **Files REMOVE:** none (their standalone logic is replaced in place, not deleted-then-rebuilt).
- **Dependencies:** J0 not required — this stage is independent infrastructure work.
- **Tests:** regression tests on both files' existing (already-shipped) behaviors, plus new Result-Envelope-status tests matching the pattern in `test_app_audio_control.py`/`test_office_control.py`.
- **Definition of done:** zero standalone `subprocess`-based automation logic remains in either file.
- **Unlocks:** J3 (a clean single dispatch surface makes method-preference language coherent to write).

### J3 — Control-Method Selection
- **Goal:** Tool-description language changes only (§ 10) — no new code module.
- **Files MODIFY:** `main.py` (tool descriptions).
- **Dependencies:** J2 (a consolidated controller makes the description coherent).
- **Tests:** a battery of "could be done multiple ways" requests, checked for consistent method choice.
- **Definition of done:** method selection is phrasing-independent, verified by test.

### J4 — Plan → Act → Verify
- **Goal:** Wire `task_engine.py` (J0) into real multi-step objectives; add end-of-objective verification.
- **Existing components reused:** `_handle_tool_batch`'s proven ordered execution (§ 4/§ 6) — this is why J4 is smaller work than the earlier roadmap implied; the execution plumbing is already solid.
- **Files MODIFY:** `main.py`, `task_engine.py`.
- **Dependencies:** J0, J2, J3.
- **Tests:** a held-out set of realistic multi-step objectives (mocked actions), verifying the FINAL report matches the ORIGINAL objective, not just the last step.
- **Definition of done:** matches the earlier roadmap's own J4 exit criteria.
- **Unlocks:** J5, J6, everything downstream.

### J5 — Recovery
- **Goal:** Tiered method-fallback (§ 13), extending the ONE existing recovery path.
- **Files MODIFY:** `computer_control.py`.
- **Dependencies:** J3 (needs the method hierarchy to be explicit), J4 (needs step records to log recovery attempts against).
- **Tests:** deliberately induced ambiguous/failed cases, confirming escalation through tiers and a bounded, honest give-up.
- **Definition of done:** matches the earlier roadmap's J5 exit criteria.

### J6 — Computer/Application Perception
- **Goal:** Composed pre-action state query (§ 6's INSPECT stage).
- **Files MODIFY:** `computer_control.py`.
- **Existing components reused:** `get_active_window_title`, `list_ui_elements`, `screen_processor.py`'s capture — all confirmed existing, just not yet composed into one query.
- **Dependencies:** J4 (multi-step plans are what actually need to consult state up front).
- **Definition of done:** matches earlier roadmap's J6 exit criteria.

### J7 — Terminal & File System
- **Goal:** Generalize `system_shortcuts.py`'s registry pattern to safety-tiered file/shell operations.
- **Files MODIFY:** `system_shortcuts.py`, `file_controller.py`.
- **Dependencies:** J2 (shared safety-tier discipline).
- **Definition of done:** a red-team test set of destructive requests are all correctly blocked/confirmation-gated — the most conservative test bar in the whole roadmap, per the earlier roadmap's own note.

### J8 — Software Development Agent *(materially re-scoped by this session's code tracing — see § 5 Example 2 and § 8's CREATE section)*
- **Goal:** `repo_agent.py` (new) — repo-wide search + existing-project test-run integration, reusing `code_helper.py`'s file-level primitives and `dev_agent.py`'s proven classify-fix-reverify PATTERN.
- **Files CREATE:** `repo_agent.py`.
- **Files MODIFY:** `code_helper.py` (expose its functions as reusable primitives), possibly bring its/`dev_agent.py`'s verification onto `result_envelope.py` (§ 12).
- **Dependencies:** J4, J5, J7.
- **Definition of done:** a deliberately introduced, realistic bug in a real (non-generated) test repo is diagnosed and fixed end-to-end, verified by that repo's own test command.
- **Risk note:** this is the stage where the earlier roadmap's assumptions were LEAST accurate before this session's tracing — treat its scope as genuinely larger than previously estimated.

### J9 — Git
- **Goal:** `git_control.py` (new) — the one universally missing primitive, confirmed by direct search (zero existing git code).
- **Files CREATE:** `git_control.py`.
- **Dependencies:** J8 (commit's SAFE status is gated on tests actually passing).
- **Definition of done:** matches earlier roadmap's J9 exit criteria; force-push/history-rewrite provably BLOCKED, not just confirmation-gated.

### J10 — Autonomous Multi-Step Technical Objectives
- **Goal:** Integration capstone — composition of J4+J5+J6+J8+J9, no new primitives.
- **Definition of done:** matches earlier roadmap's J10 exit criteria.

### J11 — Deployment & Production Operations
- **Goal:** A formalized deploy-and-verify primitive, modeled on this project's OWN already-practiced manual habit (commit → push → verify via a real HTTP check against Render).
- **Files CREATE:** a deployment-operations module (name TBD at implementation time).
- **Dependencies:** J9, J8.
- **Definition of done:** matches earlier roadmap's J11 exit criteria; deploy stays CONFIRMATION-REQUIRED permanently.

---

## 17. SARANA Relationship

Unchanged from the approved roadmap, restated precisely against confirmed code:

- **Mode switch:** `jarvis_mode` tool (existing, confirmed), gates `computer_control.py`'s higher-autonomy actions (`accomplish`, `ui_find`, `ui_click`, etc.) — confirmed via `test_accomplish_blocked_without_jarvis_mode` in the prior audit.
- **SARANA handles a task itself when:** the request maps to a SAFE-tier, single-step, low-ambiguity action or is conversational/research in nature.
- **SARANA hands off to JARVIS when:** the request needs multi-step planning (J4+), elevated system access, or technical/development work — SARANA's correct behavior is to say so and offer the mode switch, never to attempt a degraded version of JARVIS's job.
- **Shared memory:** personal/preference memory (existing `memory/*`), shared by both modes.
- **Mode-specific memory:** task/execution memory (roadmap § 12) — JARVIS-only, kept in a separate logical namespace on the same storage backend.
- **Safety/autonomy difference:** SAME classifier (`result_envelope.is_consequential`) for both modes — SARANA simply never reaches the tools that would trigger the higher tiers, because JARVIS-mode gating already prevents it from calling them at all.

---

## 18. Safety

| Category | Today | J-stage that touches it | Tier |
|---|---|---|---|
| Deletion | `send2trash`-based (per Phase-0 evidence), CONFIRMATION-gated in principle | J7 | CONFIRMATION → BLOCKED for anything outside the recycle bin |
| Messaging | `send_message.py` | J2 (migration only, not a tier change) | CONFIRMATION (unchanged) |
| Shutdown/restart | `computer_settings.py`, already CONFIRMATION-gated via `result_envelope.is_consequential` | — | CONFIRMATION (unchanged) |
| Git commit | *(new, J9)* | J9 | SAFE, but ONLY once J8's test-run verification passes |
| Git push | *(new, J9)* | J9 | CONFIRMATION REQUIRED, always |
| Force-push / history rewrite | *(new, J9)* | J9 | **BLOCKED BY DEFAULT, permanently** |
| Deployment | *(new, J11)* | J11 | CONFIRMATION REQUIRED, permanently — never promoted to SAFE regardless of JARVIS maturity |
| Purchases | Not currently implemented anywhere | — | Would be BLOCKED BY DEFAULT if ever added — out of scope for this roadmap |
| Arbitrary code execution | `desktop.py`'s `exec()` path — **the one confirmed real security gap**, no runtime sandboxing, no `is_consequential` gate today | Shared prerequisite (§ 9 of the prior roadmap) | Must become BLOCKED BY DEFAULT before J7+ meaningfully extends terminal/file capability — fixing this is a prerequisite, not a J-numbered stage itself |

The safety DECISION point remains exactly where it already is: `result_envelope.is_consequential()` / `is_confirmed()`, checked BEFORE execution in every module that adopts it. No J-stage in this plan proposes a second safety classifier or a path that bypasses this one.

---

## 19. Testing Architecture

Matching this project's own already-consistent convention (confirmed across every test file inspected this session and the prior one):

| Category | What it covers | Real side effects allowed? |
|---|---|---|
| Unit tests | Individual functions (`_ui_find`, `_parse_json_items`, `task_engine.py`'s step logic) | Never |
| Integration tests | A module's full dispatch (`computer_settings()`, `office_control()`) against mocked OS/COM calls | Never |
| Mocked computer tasks | `accomplish()`/`ui_click` against a fake UIA tree | Never |
| Safe real computer tasks | Clipboard round-trip, real temp-file pptx build (already-established exceptions in this project's own suite) | Yes — ONLY genuinely safe, reversible, local operations |
| Technical-agent tests (J8) | `repo_agent.py` against a disposable throwaway test repo/fixture, never this project's own live repo | Mocked repo state, or a disposable fixture repo |
| Recovery tests (J5) | Deliberately induced ambiguous/failed mock scenarios | Never |
| End-to-end voice tests | Full `JarvisLive` turn simulation with mocked Gemini Live responses | Never — no real audio hardware, no real Gemini call |

**Never test for real:** shutdown/restart, file deletion outside a disposable fixture, real messages, purchases (n/a today), real deployment, force-push/history rewrite, `desktop.py`'s exec() path under any real payload.

Each J-stage's minimum suite is specified inline in § 16.

---

## 20. Implementation Rule

Adopted as stated in the brief, unmodified — this document is itself the ARCHITECTURE step; each future session begins at STAGE PLAN (picking one J-stage from § 16), then IMPLEMENT → TARGETED TEST → VERIFY → confirm a real USER-VALUABLE CAPABILITY exists → only then move to the NEXT STAGE. If implementation reveals an architectural problem, the rule is: stop, document it, decide if it blocks the current stage, fix only if required, update THIS document, continue.

---

## 21. Summary & Final Answer

**A. Current architecture diagram** — § 4.
**B. Target architecture diagram** — § 6.
**C. Real voice-command execution flow** — § 5 (three traced examples, file/line cited).
**D. Task lifecycle/state machine** — § 7.
**E. File CREATE/MODIFY/REMOVE/KEEP map** — § 8.
**F. Single-source-of-truth map** — § 9.
**G. Existing → target capability map** — § 10 (control hierarchy), § 16 (per-stage).
**H. Open-source research findings** — § 15.
**I. Full J0–J11 implementation roadmap** — § 16.
**J. Dependencies between stages** — inline per stage in § 16; J0→J2→J3→J4→{J5,J6}→J7→J8→J9→J10→J11.
**K. Testing strategy** — § 19.
**L. Biggest architectural risks** — (1) `main.py` is the single most-depended-upon file in the system — every J-stage that touches it (J0, J3, J4) must be additive and conservative, never a rewrite; (2) J8 was the stage most likely to be mis-scoped without direct code tracing, and was — this document corrects that; (3) `desktop.py`'s unsandboxed `exec()` remains the one real, unaddressed security gap and is a prerequisite for trustworthy J7+ terminal/file expansion, not an optional cleanup item.
**M. Explicit do-not-build list** — a second orchestration/agent framework underneath `JarvisLive`; a second tool-execution queue (one already exists and is correct); a second per-call state dict; a second verification/status vocabulary; app-specific one-off scripts for new apps; arbitrary unsandboxed code execution; a second memory system; a second browser controller; a rigid hard-coded control-method selector (let tool-description language guide Gemini's own choice, as it already successfully does elsewhere in this codebase).
**N. First implementation stage** — **J0 (Core Task Lifecycle)**, specifically because it is the only stage in this entire plan with zero user-visible behavior change and the smallest possible blast radius, while being a hard dependency for J4 and everything downstream of it. Land it, verify the existing test suite is untouched, and only then proceed to J2 (which can run in parallel with J0 being verified, since it touches different files).

### Final answer

> **Can we continuously upgrade JARVIS for years without repeatedly redesigning its core?**

**Yes, with one condition made explicit by this tracing exercise:** `main.py`'s `JarvisLive` class and its tool-dispatch plumbing (`_execute_tool`, `_tool_call_queue`, `_handle_tool_batch`) must remain the single, unforked execution core for the entire roadmap — every stage in § 16 was deliberately designed to extend it, never fork it. The only genuinely new long-lived subsystem introduced across all 12 stages is `task_engine.py` (J0), and even that is designed to sit ON TOP of the existing batch-execution plumbing, not replace it. If a future session ever proposes a second tool-dispatch loop, a second verification vocabulary, or a second agent framework "underneath" `JarvisLive` to implement any later stage, that is the signal this condition has been violated — and per § 20's own rule, that should stop, get documented, and get resolved by extending this document, not by building around it.
