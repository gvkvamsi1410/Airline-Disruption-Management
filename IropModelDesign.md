# IROP Tail-Swap Recovery — Model Design

## 1. The decision

It is mid-morning. One aircraft has broken. For **every flight that has not yet
departed**, decide:

| Knob | Variable | Meaning |
|---|---|---|
| Assign | `x[t,f] ∈ {0,1}` (subtype-eligible pairs only) | tail `t` operates flight `f` |
| Cancel | `cancel[f] ∈ {0,1}` | flight `f` is dropped |
| Delay  | `dep[f] ∈ ℤ`, `sched_dep(f) ≤ dep[f] ≤ sched_dep(f)+MAXDELAY` | actual departure time (minutes from midnight) |
| Sequence | `seq[t,f1,f2] ∈ {0,1}` (geographically valid, time-feasible pairs) | tail `t` flies `f1` then immediately `f2` |

## 2. Data (the `Parameter` table)

`turnMin = 45` · `maxDelayMin = 360` · `bigM = 2000`
`wDelay = 1` per minute · `wCancel = 2000` per flight · `wSwap = 150` per reassigned flight
`forbidSwaps` — what-if lever for the manual baseline

The disruption is a row in the `Disruption` table: `(tail, unavailable_from, unavailable_until)`.

## 3. Objective

```
minimize   wDelay  · Σ_f (dep[f] − sched_dep(f))
         + wCancel · Σ_f cancel[f]
         + wSwap   · Σ_{t ≠ orig(f), f not already departed} x[t,f]
```

Delay is pay-per-minute; a swap is a flat fee; a cancellation is a large fee.

## 4. Constraints

The model supports **any number of simultaneous groundings** — the
`Disruption` table can carry more than one row, one per grounded tail, and
every constraint below already loops over however many there are. No model
change is needed to go from one disruption to several; see §8.

| Name | Statement |
|---|---|
| **coverage** | `Σ_t x[t,f] + cancel[f] = 1` for every flight |
| **freeze** | for every flight with `sched_dep < cutover(orig_tail(f))`: fixed to its original tail, on time, not cancellable — see §8 for how `cutover` is defined per tail |
| **blackout** | for each grounded tail `g` and each of its non-frozen flights `f`: `dep[f] ≥ until_g · x[g,f]` |
| **cancel-ontime** | `dep[f] − sched_dep(f) ≤ MAXDELAY·(1 − cancel[f])` — a cancelled flight adds no phantom delay |
| **turn time** | if `seq[t,f1,f2]=1`: `dep[f2] ≥ dep[f1] + dur(f1) + TURN`; and `seq ≤ x[t,f1]`, `seq ≤ x[t,f2]` |
| **rotation** | per tail: ≤1 predecessor for a flight from its start_location (may be a fresh start), **exactly** 1 predecessor otherwise (can't appear at an out-station), ≤1 successor per flight, ≤1 fresh start per day |

Together the rotation constraints force each tail's assigned flights into one
continuous chain that begins at that tail's start location — a physically
flyable rotation.

## 5. Differences from the reference `model.py`

| Reference | Here | Why |
|---|---|---|
| freezes exactly `{F201, F202}` | freezes **every flight that departed before the disruption** (10 in the sample) | otherwise the optimizer may "recover" by rewriting flights that already operated |
| weights hard-coded in the file | read from the `Parameter` table | tunable without code changes; the dashboard can expose them |
| `"ORD"` hard-coded as home base in the rotation constraints | each tail's own `start_location` | generic; also handles a tail that is not at the hub when the disruption hits |
| no bound on a cancelled flight's `dep` | pinned to schedule | keeps a cancelled flight out of the delay term of the objective |
| no solution check | every solve is walked leg-by-leg (`irop_feasibility.py`) | the handoff doc records that early drafts returned unflyable "optimal" solutions |

## 6. Sample scenario result

Tail A2 grounded at ORD 09:35–13:35 (picked via flight F202).

| | Optimized | No-swap baseline |
|---|---|---|
| Action | F203, F204 → A3 (+20 min each) | F203, F204 stay on A2 |
| Total delay | **40 min** | **390 min** |
| Cancellations | 0 | 0 |
| Cost | 340 | 390 |
| Solve | proven optimal, ~1 s, 226 vars / 584 constraints | proven optimal |

A3 is the swap target because its baseline rotation has a long scheduled layover
at ORD (F302 arr 09:55 → F303 dep 15:00). This slack is deliberately built into
the sample schedule so that a genuine tail swap is the optimal recovery.

## 7. Out of scope (unchanged from the handoff)

Crew legality/pairing, passenger rebooking cost, airport slots/curfews,
multi-day recovery, live re-optimization loop.

## 8. Multiple simultaneous disruptions

`Disruption` can hold N rows — one grounded tail each — and the coverage,
blackout, turn-time and rotation constraints already operate per-tail, so
adding a second (or third...) disruption needs no new constraint family.
Verified by an automated sweep of every pair of tails grounded together
(`tests/test_model.py`), all solving to proven optimality inside the
Community Edition cap.

**One thing that does need care: which flights are frozen.** With more than
one disruption, a single shared cutover time for every tail is wrong the
moment the disruptions happen at different times — an uninvolved tail's
flights between the two times would incorrectly stay re-plannable, even
though in reality that tail was operating normally and nothing about it was
known to be wrong yet. The fix: each tail is frozen relative to *its own*
disruption's start time if it has one, and relative to `planning_now_min`
(the *latest* disruption start in the batch — the earliest moment a joint
recovery could actually be planned around all of them) if it doesn't.

```
cutover(tail) = disrupted_tail_windows[tail].from_min   if tail is disrupted
              = planning_now_min                        otherwise
freeze flight f  if  sched_dep(f) < cutover(orig_tail(f))
```

For simultaneous disruptions (equal start times) this reduces to exactly the
single-disruption rule — `planning_now_min` equals every disrupted tail's own
start, and every tail's cutover is the same instant. The distinction only
bites when disruptions are staggered in time.

**A tail can only be grounded once per scenario.** Two `Disruption` rows
naming the same tail is not a representable situation (what would two
different blackout windows on one aircraft even mean?) — `IropDataManager`
raises a clear `ValueError` rather than silently keeping only the last row
for that tail. The dashboard checks for this before it ever builds the
`Disruption` table (`find_duplicate_tail_picks`), so a user picking two
flights on the same aircraft sees an inline rejection instead of a broken
scenario reaching the solver.
