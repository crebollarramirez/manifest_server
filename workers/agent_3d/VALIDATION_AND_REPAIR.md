# Validation and repair

How the CAD agent proves its work is correct, and what happens when it isn't.

This is the conceptual guide. `AGENT_REASONING.md` covers the reasoning loop
these boundaries sit inside; `README.md` covers the job lifecycle around them.

## The problem this solves

The agent writes CadQuery source. Source that parses is not source that
builds, and source that builds is not source that builds *what was asked
for*. Something outside the model has to check.

Before this existed, that check happened once, at the very end. A candidate
that failed it took the whole job down — every correct turn the agent had
spent was discarded, and the validator's diagnostics were never shown to the
thing that could have acted on them. The agent was writing code with no
compiler and no test suite, and finding out only after it was too late to
respond.

There are now two checkpoints and a way back from a failure at either.

## Two boundaries

```text
PS-1  agent works ──▶ "I'm done"  ──▶ [ STEP GATE ]  ──▶ completed
                          ▲                  │
                          └── diagnostics ◀──┘ failed

PS-2  ... same ...

PS-3  ... same ...
                                 │
                                 ▼
                          [ FINAL GATE ]  ──▶ commit
                                 │
                                 ▼ failed
                          append ONE repair step
                                 │
                                 ▼
       PR    agent repairs ──▶ "fixed"  ──▶ [ FINAL GATE ]  ──▶ commit
                          ▲                  │
                          └── diagnostics ◀──┘ failed (bounded)
```

**The step gate** catches a defect at the step that caused it, while the
agent still has that step's context loaded and knows what it was trying to
do. It is early feedback.

**The final gate** answers a different question: *is the integrated result of
the whole plan valid?* Not "did the last step leave something buildable."
This is the authoritative one — nothing reaches canonical source without it.

The two currently run the same validator. Keeping the boundaries separate
means the final one can grow stronger later (goal-criteria checks, geometry
assertions) without redesigning anything.

## What "the agent asked to finish" actually means

`request_step_completion` is a request, not a declaration. The orchestrator
decides, in this order:

1. **Deterministic gates.** Cheap, local checks — e.g. a step addressing a
   required criterion on a part with zero features. These run first because
   there is no point paying for a sandboxed CadQuery run to learn something a
   string scan already knows.
2. **Validation.** The candidate goes to the independent validator worker.

Only if both pass does the step become `completed`. A failure is *not* a
state transition — the step stays `in_progress`, and the diagnostics come
back as the failed result of the agent's own tool call, on the same reasoning
chain. The agent keeps everything it already established and simply corrects
course. No new step, no new reasoning stage, no restart.

That last detail is the whole trick: the repair mechanism for a step is the
normal loop, reused. It needed no new machinery.

## One part, one solid

Passing the validator is necessary, not sufficient. The validator answers
*"does this source parse, is it safe, and does it execute to well-formed
geometry?"* — it has no opinion about whether the result is one object.

So a verdict that came back `passed` is downgraded to a repairable failure
when the report says `solid_count > 1`. **A part is exactly one connected
printable solid.** A project made of several physical pieces is several
*parts*, each with its own source, its own agent, and its own assembly
interfaces — so a part that quietly split into two bodies is a piece nothing
downstream can see, bind an interface to, or hand to another agent.

The split is deliberate: the **validator measures**, the **orchestrator
judges**. `solid_count` comes straight from the validator's own report;
"exactly one" is this system's product rule, not a general fact about valid
CadQuery, so it lives with the agent rather than the validator. A report
carrying no measurement at all is never failed on this rule — reports
predating it must not fail candidates that otherwise validated.

This costs nothing extra. The validator already builds the model, so it now
reports the full measurement — volume, bounding box, center of mass, solid,
face and edge counts, the largest planar faces with their inclination from
horizontal, and how many edges still meet at a corner — from the execution it
was doing anyway. That measurement is also handed to the next step in its
opening context, so a step begins knowing what the part currently *is* rather
than spending its first round on `check_geometry` to ask.

The face and edge census is there because the rest of that list describes
extent, not shape: a bounding box is identical for two parts whose support
faces differ by six degrees, and rounding four of forty-eight edges barely
moves a volume. Both are shipped defects the earlier measurement could not
describe. Neither gates anything — unlike the solid count, "every edge
rounded" and "this face sits at 65°" are requirements of a particular goal
rather than universal truths about parts.

The gate exists because of a specific run: the model split in two at PS-4,
validated clean four times, and the next step then spent its entire turn
budget trying to fillet two disjoint bodies together. Fillets cannot join
what does not touch.

## Not every failure is the agent's fault

A validator can fail for reasons the model cannot possibly fix — it timed
out, it crashed, or the verdict it returned describes some other candidate
entirely. Handing those to the agent as if they were CAD problems produces
confident nonsense: it will "fix" code that was never broken.

So every verdict is classified three ways:

| Outcome | Meaning | What happens |
|---|---|---|
| **passed** | This exact candidate is valid | Proof retained; step completes |
| **repairable** | The candidate's own source is at fault | Diagnostics go to the agent |
| **infrastructure** | The verdict says nothing actionable about our source | Job fails; agent never sees it |

**Identity is checked first**, before anything else. If the returned report's
path, hash, or job ID don't match what we submitted, it isn't describing our
bytes — so its "is this repairable?" flag is meaningless and its diagnostics
point at code this job never wrote. That check has to come first or
everything downstream reasons from a report about someone else's candidate.

The repairable determination leans on `repairable_hint`, which the validator
already emitted and nothing previously read. It's true for AST/decorator
failures and CadQuery runtime exceptions; false for timeouts, worker crashes,
import errors, and hash mismatches.

Two extra conditions before a failure counts as repairable:

- **The report must be schema 2 or newer.** Older reports have no
  `repairable_hint` field at all, and silently treating "absent" as
  "unrepairable" would produce a baffling hard stop with no explanation.
- **It must carry at least one diagnostic.** A repairable verdict with
  nothing to say gives the agent nothing to act on; it would burn its turn
  budget reproducing the identical failure.

## Three guards against wasted work

Validation is expensive — a queued child job and a sandboxed CadQuery
execution. Three rules keep the step gate from spending it pointlessly.

**Skip what's already proven.** If the candidate's hash matches a validation
that already passed — a step that only searched and read, changing nothing —
no run is queued.

**Refuse to re-validate identical bytes.** If the agent requests completion
again without changing anything since a failure, that's rejected outright
(`STEP_VALIDATION_NO_CHANGE`). This is load-bearing rather than an
optimization: the queueing RPC deliberately reuses the existing child job for
an unchanged path+hash, so a re-validation would return the same failure
instantly and teach nobody anything.

**Stand down after two rejections.** This one is subtle and it is the most
important rule here.

A step whose candidate keeps failing would otherwise stay `in_progress` until
it exhausted its turn budget, then fail the entire job — *mid-plan, before
ever reaching the final gate where the repair machinery lives*. That is
strictly worse than having no step gate at all: the same defect, under the
old design, would have reached the final boundary and gotten a repair step.

So after two rejections the step gate stops validating and lets the
completion through. The candidate is still broken. It now goes on to the
final gate, which is the boundary that owns repair. **The step gate is early
feedback, never a second wall.**

Set `CAD_EDITOR_STEP_VALIDATION=0` to turn off the step gate entirely — for
measuring its cost, or as an escape hatch. The final gate and repair loop are
unconditional.

## The repair step

When the final gate reports a repairable failure, the orchestrator appends
**one** step to the plan and re-enters the ordinary agent loop.

What makes this safe is what it *doesn't* do:

- The agent cannot request it. Deterministic orchestrator code reacting to a
  failed validation is the only author.
- Nothing is reordered, replaced, or deleted. It is strictly an append.
- The goal is never touched — same as everywhere else in this system.
- The planner never learns the concept exists. `kind` lives on a plan-side
  model the planning prompt never sees, so the planner is never asked to emit
  a field it has no business setting.

The step carries no completion criteria of its own. The original plan already
covers the goal's criteria; this step exists to make the integrated result
valid while preserving them.

It then runs as a completely ordinary step — fresh reasoning chain, the same
twelve tools, the same loop — with the failure's diagnostics supplied as
context. Its completion is gated by full validation, the same way, and
passing that *is* the final proof used to commit.

**One repair step holds every attempt.** A failed repair does not append
another step; it returns updated diagnostics and the same step keeps working,
up to a bounded number of attempts before the job gives up
(`REPAIR_BUDGET_EXHAUSTED`).

Repair also gets its own turn budget, separate from the main loop's. Without
that, a plan that consumed 31 of its 32 turns would hand repair a budget of
one — the repair step would exist but have no room to do anything.

### Why diagnostics don't live on the step

The plan is re-serialized into history on every step transition, and sent to
the model in full at the start of every reasoning chain. Diagnostics stored
on a step would therefore be copied into history repeatedly and re-sent to
the model on every subsequent chain, forever.

The step carries a short human-readable objective. The full record lives in
history, and reaches the repair chain as a dedicated `validation_feedback`
field — deliberately *not* disguised as a tool observation, because
observations mean "calls you made," and lying to the model about where a fact
came from is exactly how confused repair attempts start.

## Surviving a crash

A worker can die at any point; another picks the job up from durable state.
Validation and repair both have to survive that.

**Every verdict is written to history.** A replacement worker replays them to
rebuild which hash last failed, how many rejections the gate has spent, and
what the diagnostics were — so it doesn't re-pay for verdicts the previous
worker already obtained.

**Every run gets its own immutable snapshot.** Validation reads the candidate
into a run-scoped path that is written once and never rewritten. That's what
makes it safe to carry a step's proof forward to commit: the proof keeps
describing the exact bytes the validator saw, even after the agent has edited
the live candidate since. Commit re-verifies that proof independently
regardless of which gate produced it.

**The repair append orders its writes for crash safety.** The trigger record
and diagnostics are written first; the updated plan is written *last*, as the
commit point. A worker that dies before that final write leaves a plan with
no repair step — so the resumed worker re-validates the same unchanged bytes,
gets the same verdict, and appends cleanly. A worker that dies after it finds
the repair step already active and simply runs it. Either way: exactly one
repair step.

## Failure codes

| Code | Meaning |
|---|---|
| `CANDIDATE_VALIDATION_FAILED` | Agent-facing. The step isn't done; diagnostics attached. |
| `DISCONNECTED_SOLIDS` | A diagnostic inside the above: `build_model` returns more than one solid. |
| `STEP_VALIDATION_NO_CHANGE` | Agent-facing. Completion re-requested on bytes that already failed. |
| `GEOMETRY_CHECK_FAILED` | Agent-facing. `check_geometry` measured nothing; reason attached. Gates nothing. |
| `VALIDATION_FAILED` | Terminal. An infrastructure failure, or a non-repairable final result. |
| `REPAIR_BUDGET_EXHAUSTED` | Terminal. Still invalid after the repair budget was spent. |
| `REPAIR_PLAN_FULL` | Terminal. No room in the plan for a repair step. |

The first two are tool results the agent reads and responds to. The last
three end the job; canonical source is untouched in all of them.

## Watching it happen

Everything lands in the job's existing JSONL trace — no separate log.

```bash
# every validation run and repair append, in order
jq 'select(.event | startswith("validation") or startswith("repair"))' \
  logs/<edit_job_id>.trace.jsonl

# did any completion request get rejected, and why?
jq -s '[.[] | select(.event == "tool.completed"
                     and .tool_id == "request_step_completion")
        | {step_id, ok, code: .result.error.code}]' \
  logs/<edit_job_id>.trace.jsonl
```

A validation that was skipped as provably redundant emits no events at all —
which is precisely the signal that it was skipped.
