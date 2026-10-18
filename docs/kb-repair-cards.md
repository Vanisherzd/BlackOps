# Knowledge pack v4: repair cards for this stack

## The problem

The pack's 1,557 cases (Cloud-OpsBench, RCAEval, AgenticOpsEval, ITBench-Lite) say what broke and never how it was
fixed: 85 of 1,581 documents mention any repair word. Its 17 cards carry fixes, but as `kubectl` and shell commands
the agent cannot run. And the agent only ever sees cards: `knowledge_note` and `search_incident_knowledge` print a
card's CHECK and FIX lines, cases appear as titles. So the pack could at best name a fault, never say what to do.

## What v4 adds (`scripts/kb-cards.py`, `kb/cards/`)

- 14 repair cards, one per public fault family (122 fault types merged), with checks and fixes in this stack's own
  tools, an `escalate` line for what no tool fixes, and the public fault types each covers. 1,550 of 1,557 cases now
  link to a card (`metadata.repair_card`). One card collects the Kubernetes-only families (scheduling, quotas,
  probes) and says there is no equivalent here.
- Symptom phrases written the way the agent queries, each checked against the phrase bar (`kb-cards.py phrases`):
  the tokenizer drops numbers and common words, so "api returns 500" is `api returns` and would also fire on 404.
- 17 verified fixes from this stack's drills (`kb-cards.py experience`): runs the agent fixed on its own, named the
  cause and made no extra state change. They become `stack-<case>` cases and `proven_here` lines on their card.
  Root-cause sentences that name the wrong mechanism are dropped: in all five clean upstream-sigkill runs the agent
  called it an OOM kill, so that record has a fix and no cause.
- Agent: `KB_RETRIEVAL=v4` selects it (v3 stays the default); six card candidates instead of three, or a stack card
  outranked on BM25 by a generic card never reaches the phrase test.

Retrieval check (`kb-cards.py eval`): on the public held-out incidents v4 pushes one card in 60 (v3: none), so the
v3 evaluation is unaffected. On the agent's 39 distinct drill queries v4 pushes 10 cards, all of the right family;
v3 pushes none.

## Drill A/B on blind batch 3 (2026-10-18, `logs/ab-20261018-171405-{A,B}.log`)

Four faults written after the cards were frozen (`logs/kb-cards-frozen-*.sha256`), two chosen so that a card could
mislead. 3 runs per case per arm, arms alternating, original Nano NVFP4.

| case | v3 | v4 | card reached the agent |
|---|---|---|---|
| upstream-perm-denied (403) | 0/3 | 0/3 | v4: 2 runs, the right card |
| upstream-sigstop (504, container Up) | 2/3 | 3/3 | no card of its kind |
| upstream-429 | 2/3 | 2/3 | none |
| front-mime-missing (file deleted from the image) | 0/3 | 0/3 | none |
| total | 4/12 | 5/12 | 2 of 12 runs |

No difference beyond noise. Why, run by run:

- The card rides on searches, and the agent searched in 6 of 12 v3 runs and 9 of 12 v4 runs, often with bare
  codes ("429", "504") that match no phrase.
- Where the agent did find the right fix, the guards stopped it. perm-denied: card, restart, then
  `recreate_stack replace=true`, rejected by the reviewer. 429: recreate rejected because the runbook's condition
  says 5xx. front-mime-missing: `recreate_stack edge-victim replace=true` denied by the tool itself ("the config is a
  host file, a rebuild keeps the fault"), which is true for default.conf and false for a file of the image, so this
  case cannot be fixed under the current rules.

The bottleneck on unseen faults is the guards' conditions, written for the faults already known, and the card only
arriving when the agent happens to search.
