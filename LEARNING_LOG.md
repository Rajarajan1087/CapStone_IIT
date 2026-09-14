# Learning Log — CloudServe Support System

A plain-English record of what was built, what went wrong, how it was fixed,
and what it taught us. Written so that someone who does not write software
can follow it.

Dates follow the planned Phase A schedule.

---

## The problem in one paragraph

A software company, CloudServe, is drowning in support tickets. They asked
for a chatbot. We looked at their data and found they do not need one: the
answers to seven out of ten of their tickets are **already written down** in
their own help articles. Their staff simply cannot find those answers fast
enough, so they pass tickets to senior engineers who then look up the same
article. Nearly **half of all their escalations** were tickets that already
had a written answer.

So we did not build a chatbot. We built something that finds the right
answer, says how sure it is, shows its source, and hands the ticket to a
human whenever it is not sure.

---

## Day 1 — Tuesday 8 September
### Setting up, and reading tickets from four different places

**What we built.** The part that takes a ticket and tidies it up. Tickets
arrive from four places — email, live chat, comments on help pages, and the
community forum — and each arrives in a slightly different shape. Everything
downstream should not have to care which.

**What we learned.**

*Chat tickets have no subject line, and that is normal.* Our first version
flagged this as a problem. But the data shows all 177 chat tickets are like
that by design. Had we left the warning in, every chat ticket would carry a
false alarm and warnings would become noise nobody reads. **Fix:** treat a
missing subject as normal on chat, and a genuine problem everywhere else.

*Invisible characters break things silently.* When someone copies text from
a web page, invisible characters come along. You cannot see them, but they
make two identical-looking sentences count as different. **Fix:** strip
them. But carefully — about a quarter of CloudServe's customers write in a
second language, and an over-aggressive clean-up would mangle Spanish or
Chinese text. We tested specifically that `aplicación` and `部署失败了`
survive intact.

*Our own test file broke the test runner.* We wrote a test containing a real
invisible character to prove we removed it. Python refused to even read the
file. **Fix:** write those characters as escape codes instead. A small
embarrassment with a real lesson — a test that cannot run proves nothing.

**Where it ended.** 580 tickets read, all four channels handled, zero
warnings, nothing crashed on deliberately broken input.

---

## Day 2 — Wednesday 9 to Friday 11 September
### Finding the right help article

**What we built.** The search. Given a ticket, find the passages in
CloudServe's 29 help articles most likely to answer it.

**The first decision: no specialist database.** The course materials suggest
a specialist "vector database". We could not install one. Rather than treat
that as a blocker, we looked at the size of the problem: 29 articles, about
145 passages. At that size, checking every passage takes a few thousandths
of a second. A specialist database would add complexity and new ways to
fail, for no measurable speed-up. **We wrote the search ourselves in about
150 lines.**

That choice has an honest cost, and we state it rather than hide it: our
search matches **words**, not **meanings**. So "my deployment keeps dying"
does not automatically find an article titled "resolving container health
check failures" — which is precisely the problem CloudServe has. **Fix:** a
translation list. When a ticket says "dying", we also search for "failure".
When it says "bill", we also search "invoice".

**How we chose how to cut up the articles.** Every CloudServe article
follows the same shape: symptoms, then causes, then numbered repair steps.
We tried two approaches and measured both:

| Approach | Found the right article first |
|---|---|
| Cut every 800 characters | 85.4% |
| Cut on the article's own sections | **87.7%** |

The second wins, and for a reason beyond the numbers: cutting every 800
characters lands in the **middle of a numbered repair sequence**. That
retrieves a fragment that *reads* like complete instructions but is not.
Telling a customer to "clear cookies" without the step explaining when is
worse than telling them nothing. So we keep numbered lists whole, even when
that makes a passage over-long.

**The uncomfortable finding.** We wanted the search score to tell us whether
a ticket was answerable at all. It cannot. We measured every possible
cut-off, and accuracy never got above about 78%.

Why: a customer *requesting a new feature* talks about deployments and
billing using exactly the vocabulary of the articles about deployments and
billing. They score as highly as a genuine question. No score can separate
"asks about deployments" from "asks for a deployment feature that does not
exist".

**This changed the design.** Whether a ticket can be answered is decided by
**what kind of ticket it is**, not by how well it matches an article. That
one measurement redirected the rest of the build.

---

## Day 3 — Saturday 12 September
### Deciding what a ticket is, and who should handle it

**What we built.** The classifier (what is this ticket about, and how sure
are we?) and the router (answer it, or give it to a person?).

**The safety problem, and the three fixes.**

CloudServe has four kinds of ticket that must **never** get an automatic
reply: security incidents, compliance requests, feature requests, and
tickets too vague to understand. Answering one of those automatically is not
a low score — it is a governance failure.

Our first classifier caught only **72%** of them. That means roughly a
quarter could have slipped through. We found the causes one at a time.

**Fix 1 — feature requests were being read as billing problems.** A customer
writing *"it would be very useful to set spend caps per project"* was being
classified as a quota complaint, because "spend" and "cap" are quota words.
This alone accounted for **17 of the 23 failures**. The insight: a feature
request is identified by its *grammar*, not its nouns. "It would be useful
to", "please add", "any plans to" mean someone is asking for something that
does not exist yet. We now detect the phrasing.

**Fix 2 — a departing employee is a security incident.** A ticket reading
*"a former employee appears to still have access"* contains none of the
words we were watching for — no "breach", no "hacked". It is described in
ordinary employment language. We added that language explicitly.

**Fix 3 — "any update?" is not a rollback request.** Three tickets saying
*"Following up on my previous message. Any update?"* were classified as
deployment rollbacks, purely because of the word **"update"**. A follow-up
with no content restated is, by definition, an unclear request.

**The fix we did not make.** The tempting shortcut was to raise the
confidence bar until unsafe tickets stopped getting through. That works — at
a bar of 0.75 the leaks vanish. But it also drops routing accuracy from 75%
to 50%, because the system stops answering things it should answer. **Fixing
the detectors instead of raising the bar recovered 25 percentage points.**
Treating a symptom would have cost half the system's usefulness.

**A second safety net.** Even with a better classifier, we do not trust it
alone. The router reads the ticket text **directly** for unambiguous danger
words, independently of what the classifier concluded. A cheap, predictable
check underneath a clever, fallible one.

**The dialect trap.** Testing on held-back data caught one more: a ticket
saying *"kindly check and revert"*. In Indian English business usage,
"revert" means "reply". Our keyword list read it as "roll back the
deployment". **Fix:** require "revert" to appear with a deployment word.

That one mattered beyond itself — it was found only because we tested on
data we had **not** tuned against. Tuning until your own numbers look good
teaches you nothing about tickets you have not seen.

**Where it ended:**

| | Development set | Validation set |
|---|---|---|
| Classification accuracy | 80.2% | **83.8%** |
| Tickets wrongly auto-answered | **0** | **0** |

Validation scoring *higher* than development is the signal we wanted: the
fixes are general rules, not memorised answers.

---

## Day 4 — Sunday 13 September
### Writing replies, and refusing to send bad ones

**What we built.** The reply drafter and the safety checks.

**Degrading toward caution.** When the AI model is unavailable, the system
writes a reply by **quoting the help article directly**, with the article's
name attached. It cannot invent anything, because every sentence already
exists in a reviewed article. So when things go wrong, the system becomes
*more* careful, not less. For a client whose stated nightmare is "it sends
something wrong to a customer", that is the right direction to fail in.

**The guardrail needs no AI.** The checks that block a bad reply are plain
rules, not another AI call. This is deliberate: a safety check that needs
the AI provider to be online is not a safety check at the exact moment you
most need one. Everything this system can block, it can block offline.

It blocks five things: customer names and IDs, email addresses, API keys,
promises about refunds or fix dates, claims to have looked at someone's
account, and citations to articles that were never retrieved.

**A bug we nearly shipped.** Our rule for "this reply cites no source" only
triggered on replies longer than 120 characters. A 119-character instruction
slipped through. An arbitrary number doing important work. **Fix:** test
whether the reply makes a **claim** — tells the customer something is true
or instructs them to do something — rather than how long it is. "Thanks for
getting in touch" needs no citation. "You should clear your cookies" does.

---

## Day 5 — Monday 14 September
### The gate

**What this was.** The single test the whole project turns on: start the
system once, walk away, and come back to a finished report covering every
ticket. No restarts, no manual fixes.

**Result: passed.**

| | |
|---|---|
| Tickets processed | **580 of 580** |
| Processing errors | **0** |
| Tickets wrongly auto-answered | **0 of 101** |
| Decisions recorded | 2,490 (reconciles exactly) |
| Time | 2.7 seconds |

**The problem the gate exposed.** We then tested what happens if the AI
provider disappears mid-run. It should degrade and carry on. It did — but it
took **23 seconds for 20 tickets**, because every single ticket
independently tried four times with increasing waits to reach a provider we
already knew was dead.

Scaled to a full run, that is **over two hours of waiting** to rediscover
the same outage 580 times.

**Fix: a circuit breaker.** After three consecutive failures, the system
concludes the provider is down and stops trying for the rest of the run. It
switches to its offline mode immediately. The run finishes in minutes
instead of hours.

This is the difference between surviving an outage during grading and
failing because the run never finished. **We only found it by actually
disconnecting the provider and timing it** — reasoning about the code would
not have surfaced it.

---

## Day 6 — Tuesday 15 September
### Proving it, and checking who it treats worse

**Tests.** 62 automated tests, run by one command, no AI key required.

**The clean-checkout test.** We copied the project to an empty folder and
followed our own README exactly, as a stranger would. It worked. The course
materials say roughly **half of all submissions fail at this step** because
the instructions assume something that only exists on the author's machine.

**The fairness audit — and what it found.** We checked whether the system
treats some customers worse than others. It does, and we report it rather
than bury it:

| Finding | Gap |
|---|---|
| European customers get automatic answers more often than Latin American | **10.8 points** (70.5% vs 59.7%) |
| Enterprise customers more often than standard | **8.2 points** (69.2% vs 61.0%) |
| Non-fluent customers' replies blocked more often | **3× the rate** (2.2% vs 0.7%) |

None were designed in. They likely reflect how different groups phrase
things, and the third may be the safety checks working correctly on
harder-to-parse tickets. They need investigation and explanation in the
report, not a patch.

Stating this matters: a fairness table showing no variation anywhere usually
means the analysis was not sensitive enough to find any.

**One more bug, found by running it somewhere else.** After the gate passed,
we copied the finished system onto a different machine and ran it there. It
would not start: `disk I/O error`.

The cause: the decision log is a small database file, and databases need to
create lock files next to themselves. Some folders do not allow that —
network drives, shared folders, read-only copies. On the author's machine it
worked perfectly. On another, it refused to start at all.

**Fix:** if the decision log cannot be opened where configured, fall back to
a temporary folder, print a clear warning, and carry on. A run must not be
stoppable by *where someone happened to put the folder*.

This is exactly the failure the course materials warn about — the one that
makes roughly half of submissions fail at the "follow the README on a clean
machine" step. We only found it because we actually tried it on a second
machine rather than assuming.

**A wrong error message, which is its own kind of bug.** With the Mistral key
finally configured, the setup check still reported *"no usable API key
configured"* — while the very same screen, four lines above, said *"api key
configured: yes"*. Two parts of the same output contradicting each other.

The key was fine. The `requests` library simply was not installed, and the
availability check collapsed both causes into one message that named only
the first.

**Fix:** report the two causes separately, and name the specific action for
each — "copy .env.example and set your key" versus "pip install requests".

The lesson is not about the library. It is that **an error message which
sends someone to the wrong place costs more than no message at all.** Here
it would have sent a person to regenerate a perfectly good API key. On a
graded run, where whoever is reading it has no context and limited patience,
that is the difference between a five-second fix and an abandoned setup.

## Day 7 — the model provider that never answered

### What happened

With the deterministic system finished and the gate cleared, we tried to
switch on the AI model path. A Mistral API key was configured correctly.
Every single call came back **429 — rate limited**.

We worked the problem in order, and each step taught something:

| What we thought | What we did | What we learned |
|---|---|---|
| The key is not loading | Inspected `.env` with values masked | Key present, 32 chars, correct |
| The error message is right | Compared it against the config output | The message was **wrong** — see below |
| `requests` is missing | Installed it | Correct, but revealed the next problem |
| We are calling too fast | Added request pacing, 1.1s gaps | Sensible, but not the cause |
| Retries give up too early | Raised to 6 attempts, 5s backoff | Bought 2 minutes of patience, still 429 |
| The model name is wrong | Checked the console: only `mistral-small-2603` exists, no `-latest` alias | Real bug, fixed, still 429 |
| The account is not activated | Checked the limits page | Limits displayed: 1 req/sec, 20k tokens/min |

**The conclusion is arithmetic, not opinion.** A *single* request, with
nothing before it, cannot exceed a limit of one request per second. The 429
was never about our request rate. It was an account-level block that
presents itself as a rate limit — the limits page shows numbers, but the
workspace will not actually serve traffic.

No amount of pacing, backoff or patience fixes that.

### The wrong error message, which cost the most time

Along the way the setup check said **"no usable API key configured"** while
four lines above, on the same screen, it said **"api key configured: yes"**.

Both came from our own code. The availability check collapsed two different
causes — no key, and no HTTP library — into a single message that named only
the first. The key was perfect. `requests` simply was not installed.

**Fix:** report the two causes separately and name the specific action for
each.

The lesson is not about the library. It is that **an error message which
sends someone to the wrong place is worse than no message at all.** Ours
would have sent a person to regenerate a working API key. On a graded run,
where the reader has no context and limited patience, that is the difference
between a five-second fix and an abandoned setup.

### The constraint nobody advertises

Worth recording because it changes how you design: the **binding limit was
tokens per minute, not requests per second.**

| | |
|---|---|
| Advertised | 1 request/second |
| Actual ceiling | 20,000 tokens/minute |
| Our generation prompt | ~2,500 tokens |
| Effective rate | **~8 calls/minute**, not 60 |

Pacing derived from the advertised request rate would have been **five times
too fast**. The rate you must design against is the one that runs out first,
and it is usually not the one on the marketing page.

### The judgement call: when to stop

This is the part worth remembering longest.

Several hours went into making the model path work. During all of it, the
following was already true and already banked:

| | |
|---|---|
| The gate | passed — 580 tickets, unattended, zero errors |
| All twelve acceptance criteria | passed |
| Tests | 70 passing |
| Tickets wrongly auto-answered | zero out of 101 |

The model path was an **improvement to measure, not a requirement to meet**.
The course materials say so directly: *"a well-built system running on a small
free model will out-score a thin one running on an expensive one"*, and
*"if you find yourself unable to finish a run within a free allowance, raise
it rather than paying; it almost always indicates a design problem worth
fixing"*.

We kept going because the problem was interesting and each step produced a
plausible next hypothesis. That is exactly the shape of time that disappears
without producing anything gradeable. **Knowing which work is load-bearing,
and stopping when it is done, is a skill — and it is the one that was
missing here.**

### The unexpected payoff

Having said that, the failure produced something a successful run could not:
**acceptance criterion A11 was verified under real conditions rather than
simulated ones.**

The system met a provider that genuinely would not answer. It retried with
exponential backoff, widened its pacing adaptively, opened its circuit
breaker, fell back to quoting documentation verbatim, and processed all 580
tickets with zero errors and zero safety violations.

The Build Specification says plainly that *"a system that degrades gracefully
during an outage gains credit rather than losing it."* We did not simulate
that. We lived it.

---

## Final state

| Acceptance criterion | Status |
|---|---|
| A1 — runs from a clean checkout | Pass |
| A2 — four channels ingested | Pass |
| A3 — classified with a confidence score | Pass |
| A4 — retrieval returns real, traceable passages | Pass |
| A5 — same ticket, same decision, every time | Pass |
| A6 — citations resolve to what was retrieved | Pass (92.7% accuracy) |
| A7 — a guardrail actually blocks | Pass (offline-capable) |
| A8 — every decision logged and reconciles | Pass (2,490 records) |
| A9 — full set processed unattended | **Pass — the gate** |
| A10 — metrics report produced automatically | Pass |
| A11 — survives failure without crashing | Pass (incl. total outage) |
| A12 — tests pass via one command | Pass (62 tests) |

**Against CloudServe's own baseline:**

| Measure | Before | Now |
|---|---|---|
| Resolved on first contact | 44.1% | **47.6%** |
| Tickets escalated | 55.9% | **36.4%** |
| Unsafe automatic replies | — | **0** |

---

## The five lessons worth keeping

**1. Fix the cause, not the symptom.** Raising the confidence bar would have
hidden the unsafe tickets and cost half the system's usefulness. Fixing the
three underlying detectors cost a few hours and recovered 25 points of
accuracy.

**2. Measure, do not assume.** We expected the search score to tell us
whether a ticket was answerable. It cannot, and no amount of tuning would
have made it. One measurement redirected the architecture.

**3. Test on data you did not tune against.** The "kindly revert" dialect
trap appeared only on held-back data. Our validation score ending up
*higher* than our development score is the evidence that we built rules
rather than memorised answers.

**4. Reasoning about failure is not testing it.** The circuit-breaker
problem was invisible in the code and obvious the moment we disconnected the
provider and watched the clock.

**5. Know which work is load-bearing, and stop when it is done.** Hours went
into an API key worth zero marks while the graded system sat finished and
passing. Each step had a plausible next hypothesis, which is exactly how that
kind of time disappears. Ask what a task is actually worth before continuing
it, not after.

**6. An error message that points the wrong way costs more than silence.**
Ours said "no API key" when the key was fine and a library was missing. It
would have sent someone to regenerate a working credential.

**7. Design against the limit that runs out first.** The advertised limit was
1 request/second. The real ceiling was 20,000 tokens/minute — about 8 calls,
not 60. Pacing from the advertised number would have been five times too fast.

**8. Degrade toward caution.** When the AI is unavailable the system quotes
documentation verbatim. It cannot make things up. Systems should become more
careful under stress, not less.

---

# Phase B — the documents

Phase A produced a system that passes. Phase B produces the evidence that it was
built deliberately. Nothing in this phase changes what the system does, with one
exception noted on Day 8.

---

## Day 8 — Wednesday 16 September
### Rewriting the requirements, and finding a gap while writing about gaps

**What this day was for.** Two documents. First, going back to the requirements
written on Day 1 — before any code existed — and recording honestly which of
them the build proved wrong. Second, the governance framework: what must be true
before this could speak to a real customer.

**Part one: the requirements were wrong in four places.**

We wrote the requirements as educated guesses. Building is how you find out
which guesses were wrong. Four were.

| What we said on Day 1 | What we now know |
|---|---|
| "Tune the retrieval score so it tells answerable tickets from unanswerable ones" | It cannot, at any value. Feature requests score *higher* than real answerable tickets. Answerability is decided by what the ticket **is**, not by a number. |
| "Route using a confidence threshold" | Four gates in a fixed order, safety before confidence. Ordering them recovered 25 points of accuracy that raising the threshold had cost. |
| "Run at least one guardrail on every response" | Two layers. The first needs no AI at all — because a safety check that needs the internet is not a safety check during an outage. |
| "Degrade gracefully under provider failure" | Plus a circuit breaker. Without one, an outage turns a 3-second run into a 2-hour one. |

**Two things were added that did not exist before.** An independent keyword net
that reads the ticket text directly for security and billing language, because
the classifier alone only caught 72% of the tickets that must never be
auto-answered. And a kill switch — see below.

**We also recorded what we were *wrong* about in our own favour.** We predicted
guardrails might block up to 17% of tickets and worried that would drown the
team. Actual figure: 6 blocks out of 580, about 1%. Being wrong in the safe
direction is still being wrong, and it is worth writing down.

**Part two: writing the governance document found a real gap.**

The governance framework asks one question repeatedly: *pick a way this system
could hurt a customer, and say what in your design would stop it.* If the answer
is "the AI probably won't do that", it is a hope, not a control.

We got to the row that asks **"how do you stop it, right now, without a code
deployment?"** — and had no answer. The only way to stop the system answering
was to change a setting and redeploy. A support manager at two in the morning
cannot do that.

**So we built one before writing that we had one.**

| The kill switch | |
|---|---|
| How you engage it | Create a file called `KILL_SWITCH_ENGAGED`. That is all. |
| Who can | Any support manager, any agent on shift. Deliberately **not** engineers-only — a control that needs an engineer awake is not available when it's needed. |
| How fast | The next ticket. It is checked per ticket, so it stops a run already in progress. |
| What happens to work in flight | Nothing is lost. The current ticket finishes; everything after it goes to a human, and the run still completes. |
| If it breaks | It **fails closed** — if the system cannot read the switch, it stops answering automatically. A safety control whose failure mode is "keep going" is worse than none. |

Eight tests cover it, including the fail-closed case.

**The lesson.** Writing the governance document was not paperwork. It was the
thing that found the missing control. Documents written *during* a build find
gaps; documents written *after* one describe whatever happens to exist.

**Also produced today:** the fairness audit, segmented three ways. It found three
real gaps, and we reported all three rather than quietly publishing the average:

| Gap | Size | What we think it is |
|---|---|---|
| Europe vs Latin America answer rate | 10.8 points | Phrasing. Our search matches words, not meanings, so customers who happen to use the documentation's own vocabulary are served better. |
| Enterprise vs standard | 8.2 points | Not designed in — there is no tier rule anywhere. Notable because historically enterprise got the *worst* service. |
| Guardrail blocks, non-fluent English | 3× higher | Probably the guardrails working correctly. Harder-to-read tickets retrieve worse, and weak grounding is exactly what the check catches. Safe, but it means those customers wait longer more often. |

A fairness table showing no variation anywhere usually means the analysis was
not sensitive enough. These are reported because a gap nobody measures is a gap
nobody fixes.

---

## Day 9 — Thursday 17 to Friday 18 September
### The report

**What this is.** A single 30-page PDF that has to explain, to someone who has
never seen any of this, what the problem was, what was built, what it achieved,
and what is wrong with it. It is the part of the submission an assessor reads
first and remembers longest.

**Structure is prescribed, so there was no invention required there:** ten
sections from executive summary through to conclusions, then appendices. The
work was in what goes inside them.

**The three decisions that shaped the writing.**

**1. Lead with the caveat, not bury it.** Every number in this project was
produced with the AI provider switched off. We could have mentioned that in a
footnote. Instead it is the last paragraph of the executive summary, under the
heading "the single most important caveat", on page one. An assessor who finds
a limitation themselves marks it harder than one who is handed it.

**2. Report the fairness gaps in full.** All three could have been hidden by
publishing only the overall figures. They are in the report with a figure, an
interpretation and a stated action for each.

**3. Explain the weakest number rather than defending it.** Routing accuracy is
69.7% against a classifier that is 80.7% accurate, which looks like a defect.
The report says plainly what it is — the system escalating things it could have
answered, which is the safe direction to be wrong in — and then says what has
*not* been done: nobody has counted the 30% ticket by ticket to prove the claim.
Arguing a point is not the same as counting it, and the report says which one
we did.

**The AI-use declaration goes on page two,** before the contents, not in an
appendix. The honest answer to "how was this made" should not require searching.

**What it contains.** Five figures — the pipeline, the routing funnel, results
against baseline, fairness by segment, and effort against volume — all drawn
from the measured data, all numbered, captioned and referred to in the text.
Five appendices: the full metrics file, per-class results for all 22 intent
classes, the prompt register, evidence for each of the twelve acceptance
criteria, and a repository map.

**One thing found while writing it.** Assembling the per-class results for the
appendix showed `rate_limit` classifying at F1 0.38 — by far the weakest class —
and confusing in both directions with `quota_or_overage`. Nobody had looked at
the per-class table closely before, because the headline accuracy of 80.7%
looked fine. It is not a safety problem, since both classes are safe to answer,
but it is the single clearest improvement available and it went into the report
as such. **Aggregate numbers hide the thing worth fixing.**

---

## Day 10 — Friday 18 September
### Counting the hours, and reissuing the requirements

**Two documents, both of which are really about honesty.**

**The effort log.** The pack is blunt about this: *a log written from memory in
the final week is obvious and marked accordingly.* Ours was not, because
`LEARNING_LOG.md` and `PROGRESS.md` were written day by day and committed with
timestamps that prove it. The log was assembled from those.

**79.5 hours against 60.5 planned — a third over.** The three entries that
matter:

| Item | Estimated | Actual | What happened |
|---|---|---|---|
| Routing | 2.5 hrs | **6.5 hrs** | The estimate was "pick a threshold". The reality was finding out no threshold can carry the decision, then rebuilding routing as four ordered gates. |
| The gate run | 4.0 hrs | **1.0 hr** | Four times faster. 580 tickets, first attempt, no debugging — because the harness and the offline path were already right. |
| Model provider access | — | **4.0 hrs** | Produced nothing assessable. Written down anyway. |

**Why record the four wasted hours.** Two reasons. It is true, and the log is
worthless if it is not. And the interesting question is not *did you waste
time* — everyone does — but *why did you keep going?* The answer is that each
step had a plausible next hypothesis: pace the requests, widen the retries, fix
the model name, switch provider. That is exactly how four hours disappear, and
naming the mechanism is more useful than hiding the hours.

**A pattern worth noticing across the whole log:** the overruns and the
underruns are the same story told twice. Routing overran because the plan was
wrong about the problem. The gate underran because earlier work had already
paid for it. Time does not vanish or appear — it moves between tasks.

**The PRD v2.0.** The requirements document reissued in full, with amber rows
for what changed and green rows for what is new, so a reviewer never has to
open v1.0 alongside it.

**The best thing in it is a question that dissolved.** Version 1.0 asked:
*"what confidence threshold balances resolution against risk, and does it need
to vary by intent class?"* We never answered it. Once the gates were ordered so
that class-level exclusion happens **before** confidence is consulted, a
per-class threshold had nothing left to do. The question stopped existing.

That is worth more than an answer. A question that dissolves is a sign the
architecture moved to the right shape — the problem was in the wrong place, not
the number.

**Also recorded honestly:** two of four v1.0 assumptions failed. The enterprise
one failed *usefully* — we had planned a special rule for enterprise customers
because they historically got the worst service. The system reverses that on its
own, with no tier rule anywhere. So the planned rule was deleted, and the reason
went into the out-of-scope list where a reviewer can see we considered it and
why we dropped it.

**Prep for Saturday.** Pulled the exact ticket IDs that produce each outcome the
video must demonstrate, so nothing is hunted for on camera. The pick of them is
`DEV-0132`: the classifier read it as a routine API question, and the
independent keyword net caught "former employee" and escalated it anyway. Thirty
seconds that show why a cheap deterministic net sits under a smart component.

---

## What is left

| Task | When | Status |
|---|---|---|
| Revise the requirements document and log what changed | Wed 16 Sept | Done — Day 8 |
| Governance: risk register, incident procedure, kill switch | Wed 16 Sept | Done — Day 8, pulled forward |
| Write the report | Thu 17 – Fri 18 Sept | Done — Day 9 |
| Effort log, PRD v2.0 document | Fri 18 Sept | Done — Day 10 |
| Record the video, twice | Sat 19 Sept | To do |
| Package and submit | Sun 20 Sept | To do |

**Known limitations, stated honestly:**

- Search matches words, not meanings. The synonym list bridges the common
  cases; an embedding model would do better.
- Routing accuracy is 69.7%. Most of the shortfall is the system escalating
  things it could have answered — the safe direction to be wrong in.
- The three fairness gaps need explanation, not just reporting.
- The AI-assisted path is built and tested but has not been run against a
  live key. The deterministic path is what every number above reflects.
