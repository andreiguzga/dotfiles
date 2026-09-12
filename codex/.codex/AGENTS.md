# Multi-agent workflow

The root agent is primarily the orchestrator. It owns the user's intent,
architecture, planning, delegation, difficult ambiguities, important decisions,
integration, and final review. Do not delegate tiny tasks when spawning and
coordinating a worker costs more than doing the work directly.

Delegate coherent exploration across multiple files to `scout`. Do not spawn an
agent for each file. Read small, known sections directly when that is quicker.
Reason from the scout's summary first, then inspect only the paths or symbols
needed to make a decision.

Use `developer` for substantial implementation and give it a focused brief with
the scope, files, acceptance criteria, and constraints. Use explicit
`gpt-5.6-luna` with low reasoning for tiny mechanical tasks when the spawn tool
offers model controls. Do not invent TOML settings to select such a worker.

Use `verifier` for useful independent review of a concrete diff and its relevant
tests. It should not reread the whole repository. Skip redundant verification,
but retain independent review when a change is risky or a test result matters.

Parallelize only genuinely independent work. Give workers the least context they
need: use `fork_turns="none"` for a self-contained brief when the tool exposes
that control, or the smallest supported recent subset. Never pass the full
conversation by default. Tool-level controls take precedence; do not represent
them as TOML fields unless Codex documents those fields.

Worker briefs and reports must stay concise:

- A scout reports paths, symbols, line references, current behavior, and open
  uncertainties.
- A developer reports changed files, checks run and their results, and remaining
  issues.
- A verifier reports concrete findings and the exact checks it ran.

Workers must complete their assigned task without delegating it again. If a
worker is uncertain, it should escalate to the root agent instead of repeating
cheap retries. Preserve meaningful validation and verify primary sources for
risky or fast-changing decisions. Summarize rather than returning whole source
files or long logs; include source excerpts only when essential.
