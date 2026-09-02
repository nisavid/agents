---
name: writing-for-people
description: Use when drafting or editing prose a person will read, such as chat messages, replies, reports, summaries, review comments, or documentation written by an agent.
---

# Writing For People

## Scope

Own how prose an agent writes to a person opens, what shape it takes,
how it asks, what it may claim, and how it is edited before sending.
This skill sets no voice; the caller's instructions supply one. Review
comments and review replies are in scope; composing pull-request titles
and bodies and resolving review threads are not.

## Ground The Draft First

Before drafting, gather what the draft rests on: what was run and what
it returned, what was decided and by whom, what the reader asked in
their words. Say what you did not check rather than filling it in.

## Open On The Point

Open by answering what the person asked or engaging what they just said
or did; when you initiate, open with what the reader most needs. A
finding opens as a plain declarative anchored on a backticked symbol or
a concrete mechanism. No scene-setting, topic labels ("Status:"), or "I
noticed". A deliverable opens with what it hands the reader (the
capability, the fix, the decision), never a defense of its own
existence; let motivation and evidence land where the reader would ask
for them.

After each point the reader silently responds with a follow-up, a doubt,
or a challenge; write the next point to meet it. End on the last
substantive point. Write a drafted artifact for a reader arriving fresh:
fold corrections in without defending against them, keep revision
feedback out, and state open questions as scope to investigate, not as
rebuttal.

## Build From Common Ground

Consider what the reader needs to understand or do, and the context and
language you share with them. Build from the nearest common ground with
only the orientation needed. Explain each term where it stands or use
plain words. Match the reader's register while staying professional. If
a message does not land, stop, rebuild shared context, and explain more
simply.

## Make One Ask, Graded To Severity

Each comment carries exactly one ask. Default to a declinable question
("Could we…?"). Use "Please" plus an imperative only for a genuine
blocker, "Let's" for obvious cleanup, and first-person conviction ("I'd
drop the count") when confident but not blocking. Say why the remedy
closes the specific failure path.

## Claim And Qualify

Use technical precision where it changes the reader's decision and
everyday verbs everywhere else. State a confident finding as a plain
declarative; qualify only claims that are genuinely uncertain, and say
what makes them so. Credit real strengths only when the credit is
load-bearing for the point; when it is, it comes before the flag. In a
reply to a reviewer, agreement is action, not a lead-in: say little and
act, and never restate the other person's comment back at them, justify
their suggestion, or import decisions from outside the thread.

## Let Content Pick The Shape

A short answer needs no scaffolding. A substantial analysis opens with
one plain sentence carrying the whole verdict, with detail descending
from it. A substantial reply is still a reply: paragraphs in sequence,
collapsed details for optional depth; heading hierarchies and tables
belong to documents. A detail earns its place only if it changes what
the reader does next. Spend emphasis in proportion to stakes, pair each
dense, load-bearing statement with a concrete instance, and mention
concrete caveats briefly. Report verification as what the reader can now
trust, naming the machinery only when they must rerun or audit the
check. When work stops at the edge of what was asked or authorized
rather than at a real blocker, say so, and end the report with what
remains and the next decision.

## Treat Evidence As A Hard Constraint

Never assert what a test, command, or system does unless your brief says
you ran it. Never commit to a position, threshold, offer, or concession
that is not in your brief. Report what you observed ("the config record
hasn't changed since June 2"), not the inference ("the update silently
failed"). When a social fact (who asked, what happened, when) is not in
evidence, ask or leave it out.

Read [evidence-in-prose.md](references/evidence-in-prose.md) when the
draft reports an outcome, makes a commitment, or describes what someone
did.

## Edit Before Sending

Run [edit-pass.md](references/edit-pass.md) on the finished draft. For
anything going to a reviewer, a shared channel, or a customer, have a
second pass hunt for tells and recheck every fact against the brief.
