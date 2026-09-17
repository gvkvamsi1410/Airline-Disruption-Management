"""
IROP Tail-Swap Recovery Demo
Synthetic data + CPLEX (docplex) MIP.
All data below is synthetic, constructed for this demo. Not sourced from any
uploaded document or real airline schedule.
"""
import json
from docplex.mp.model import Model

# ---------------------------------------------------------------------------
# 1. SYNTHETIC DATA
# ---------------------------------------------------------------------------
# Time represented as integer minutes from 00:00.
def hm(h, m):
    return h * 60 + m

AIRPORTS = ["ORD", "DEN", "MSP", "STL"]
TURN_MIN = 45          # minimum ground turn time, minutes
MAX_DELAY = 360         # max allowed delay per flight, minutes (operational bound)

# duration by origin-dest pair (symmetric), minutes
DURATIONS = {
    ("ORD", "DEN"): 150, ("DEN", "ORD"): 150,
    ("ORD", "MSP"): 70,  ("MSP", "ORD"): 70,
    ("ORD", "STL"): 65,  ("STL", "ORD"): 65,
}

# flight_id: (origin, dest, sched_dep, subtype_required, original_tail)
FLIGHTS_RAW = [
    ("F101", "ORD", "DEN", hm(6, 0),  "A", "A1"),
    ("F102", "DEN", "ORD", hm(9, 15), "A", "A1"),
    ("F103", "ORD", "MSP", hm(12, 30), "A", "A1"),
    ("F104", "MSP", "ORD", hm(14, 25), "A", "A1"),

    ("F201", "ORD", "MSP", hm(6, 30), "A", "A2"),
    ("F202", "MSP", "ORD", hm(8, 25), "A", "A2"),
    ("F203", "ORD", "STL", hm(10, 20), "A", "A2"),
    ("F204", "STL", "ORD", hm(12, 10), "A", "A2"),

    ("F301", "ORD", "STL", hm(7, 0),  "A", "A3"),
    ("F302", "STL", "ORD", hm(8, 50), "A", "A3"),
    ("F303", "ORD", "DEN", hm(15, 0), "A", "A3"),   # long scheduled layover at ORD
    ("F304", "DEN", "ORD", hm(18, 15), "A", "A3"),

    ("F401", "ORD", "DEN", hm(6, 15), "B", "B1"),
    ("F402", "DEN", "ORD", hm(9, 30), "B", "B1"),
    ("F403", "ORD", "STL", hm(12, 45), "B", "B1"),
    ("F404", "STL", "ORD", hm(14, 35), "B", "B1"),

    ("F501", "ORD", "MSP", hm(7, 15), "B", "B2"),
    ("F502", "MSP", "ORD", hm(9, 10), "B", "B2"),
    ("F503", "ORD", "DEN", hm(11, 5), "B", "B2"),
    ("F504", "DEN", "ORD", hm(14, 20), "B", "B2"),
]

FLIGHTS = {}
for fid, org, dst, dep, subtype, orig_tail in FLIGHTS_RAW:
    FLIGHTS[fid] = {
        "id": fid, "origin": org, "dest": dst,
        "sched_dep": dep, "duration": DURATIONS[(org, dst)],
        "sched_arr": dep + DURATIONS[(org, dst)],
        "subtype": subtype, "orig_tail": orig_tail,
    }

TAILS = {
    "A1": {"subtype": "A"}, "A2": {"subtype": "A"}, "A3": {"subtype": "A"},
    "B1": {"subtype": "B"}, "B2": {"subtype": "B"},
}

# ---------------------------------------------------------------------------
# 2. DISRUPTION
# ---------------------------------------------------------------------------
# Tail A2 grounded at ORD for unscheduled maintenance right after it lands
# from F202 (09:35), unavailable until 13:35 (4-hour window).
DISRUPTED_TAIL = "A2"
BLACKOUT_START = hm(9, 35)
BLACKOUT_END = hm(13, 35)
# Flights already flown before the disruption was known: fixed, not decided.
PRE_DISRUPTION_FLIGHTS = {"F201", "F202"}

# ---------------------------------------------------------------------------
# 3. COST WEIGHTS (my own assumption for the demo, not customer-provided)
# ---------------------------------------------------------------------------
W_DELAY = 1          # cost per minute of delay
W_CANCEL = 2000       # cost per cancelled flight
W_SWAP = 150          # cost per flight reassigned to a different tail

# ---------------------------------------------------------------------------
# 4. MODEL
# ---------------------------------------------------------------------------
mdl = Model(name="irop_tail_swap")

flight_ids = list(FLIGHTS.keys())
tail_ids = list(TAILS.keys())

# eligible (tail, flight) pairs: subtype must match
eligible_tf = [(t, f) for t in tail_ids for f in flight_ids
               if TAILS[t]["subtype"] == FLIGHTS[f]["subtype"]]

x = mdl.binary_var_dict(eligible_tf, name="x")          # tail t flies flight f
cancel = mdl.binary_var_dict(flight_ids, name="cancel")  # flight f cancelled
dep = mdl.integer_var_dict(flight_ids, name="dep",
                            lb={f: FLIGHTS[f]["sched_dep"] for f in flight_ids},
                            ub={f: FLIGHTS[f]["sched_dep"] + MAX_DELAY for f in flight_ids})

# 4a. coverage: each flight flown by exactly one eligible tail, or cancelled
for f in flight_ids:
    mdl.add_constraint(
        mdl.sum(x[t, f] for t in tail_ids if (t, f) in x) + cancel[f] == 1,
        ctname=f"cover_{f}"
    )

# 4b. fix pre-disruption flights to their original tail (already flown/committed)
for f in PRE_DISRUPTION_FLIGHTS:
    orig_t = FLIGHTS[f]["orig_tail"]
    mdl.add_constraint(x[orig_t, f] == 1, ctname=f"fix_{f}")
    mdl.add_constraint(cancel[f] == 0, ctname=f"nocancel_{f}")
    mdl.add_constraint(dep[f] == FLIGHTS[f]["sched_dep"], ctname=f"nodelay_{f}")

# 4c. blackout: disrupted tail cannot depart on any (decision) flight until released
for f in flight_ids:
    if f in PRE_DISRUPTION_FLIGHTS:
        continue
    if (DISRUPTED_TAIL, f) in x:
        mdl.add_constraint(
            dep[f] >= BLACKOUT_END - (BLACKOUT_END) * (1 - x[DISRUPTED_TAIL, f]),
            ctname=f"blackout_{f}"
        )

# 4d. sequencing / turn-time feasibility for each tail across the flights it flies.
# For every ordered pair of flights (f1, f2) that could plausibly connect
# (dest(f1) == origin(f2)) and are within reach given MAX_DELAY, add a
# disjunctive precedence constraint activated only if the same tail flies both
# AND f1 precedes f2 in that tail's rotation. We use a same-tail-pair binary
# "seq" variable plus big-M, restricted to geographically connecting pairs.
BIG_M = 2000
seq_pairs = []
for f1 in flight_ids:
    for f2 in flight_ids:
        if f1 == f2:
            continue
        if FLIGHTS[f1]["dest"] != FLIGHTS[f2]["origin"]:
            continue
        # even with max delay on f1 and none on f2, must remain plausible
        earliest_arr1 = FLIGHTS[f1]["sched_arr"]
        latest_arr1 = FLIGHTS[f1]["sched_arr"] + MAX_DELAY
        latest_dep2 = FLIGHTS[f2]["sched_dep"] + MAX_DELAY
        if latest_dep2 + 1 < earliest_arr1 + TURN_MIN:
            continue  # never feasible, skip creating variable
        seq_pairs.append((f1, f2))

same_tail_seq = mdl.binary_var_dict(
    [(t, f1, f2) for t in tail_ids for (f1, f2) in seq_pairs
     if (t, f1) in x and (t, f2) in x],
    name="seq"
)

for (t, f1, f2), var in same_tail_seq.items():
    # if this tail flies f1 immediately before f2 (var=1), enforce turn time
    mdl.add_constraint(
        dep[f2] >= dep[f1] + FLIGHTS[f1]["duration"] + TURN_MIN - BIG_M * (1 - var),
        ctname=f"turn_{t}_{f1}_{f2}"
    )
    # seq can only be 1 if tail flies both flights
    mdl.add_constraint(var <= x[t, f1], ctname=f"seqlink1_{t}_{f1}_{f2}")
    mdl.add_constraint(var <= x[t, f2], ctname=f"seqlink2_{t}_{f1}_{f2}")

# 4e. for each tail, every flight it flies except exactly one "first" flight
# must be immediately preceded by some other flight it flies that connects
# (this forces a single real chain matching airport continuity, not disconnected
# or overlapping legs). A tail may start its day at ORD at most once.
for t in tail_ids:
    flights_for_t = [f for f in flight_ids if (t, f) in x]
    start_indicators = []
    for f2 in flights_for_t:
        candidates = [same_tail_seq[(t, f1, f2)] for f1 in flights_for_t
                      if (t, f1, f2) in same_tail_seq]
        if FLIGHTS[f2]["origin"] == "ORD":
            # may be preceded by a connecting flight, or be a fresh start at home base
            if candidates:
                mdl.add_constraint(
                    mdl.sum(candidates) <= x[t, f2],
                    ctname=f"chain_ord_{t}_{f2}"
                )
                start_indicators.append(x[t, f2] - mdl.sum(candidates))
            else:
                start_indicators.append(x[t, f2])
        else:
            # flight departs a spoke airport: MUST be preceded by a connecting
            # flight flown by the same tail (a tail can't magically appear at a spoke)
            if candidates:
                mdl.add_constraint(
                    mdl.sum(candidates) == x[t, f2],
                    ctname=f"chain_spoke_{t}_{f2}"
                )
            else:
                mdl.add_constraint(x[t, f2] == 0, ctname=f"nopath_{t}_{f2}")
    # a tail can begin its rotation at ORD at most once per day
    if start_indicators:
        mdl.add_constraint(
            mdl.sum(start_indicators) <= 1,
            ctname=f"single_start_{t}"
        )
    # outdegree: a flight a tail flies can be followed by at most one next
    # flight for that same tail (a tail cannot branch into two flights at once)
    for f1 in flights_for_t:
        successors = [same_tail_seq[(t, f1, f2)] for f2 in flights_for_t
                      if (t, f1, f2) in same_tail_seq]
        if successors:
            mdl.add_constraint(
                mdl.sum(successors) <= x[t, f1],
                ctname=f"outdeg_{t}_{f1}"
            )

# ---------------------------------------------------------------------------
# 5. OBJECTIVE
# ---------------------------------------------------------------------------
delay_expr = mdl.sum(dep[f] - FLIGHTS[f]["sched_dep"] for f in flight_ids)
cancel_expr = mdl.sum(cancel[f] for f in flight_ids)
swap_expr = mdl.sum(x[t, f] for (t, f) in eligible_tf if t != FLIGHTS[f]["orig_tail"])

mdl.minimize(W_DELAY * delay_expr + W_CANCEL * cancel_expr + W_SWAP * swap_expr)

# ---------------------------------------------------------------------------
# 6. SOLVE
# ---------------------------------------------------------------------------
mdl.parameters.timelimit = 60
sol = mdl.solve(log_output=False)

print("Solve status:", mdl.solve_details.status)
print("Variables:", mdl.number_of_variables, "Constraints:", mdl.number_of_constraints)

if sol is None:
    raise SystemExit("No solution found - check model / CPLEX Community Edition size limits")

# ---------------------------------------------------------------------------
# 7. EXTRACT RESULTS
# ---------------------------------------------------------------------------
results = []
for f in flight_ids:
    is_cancelled = cancel[f].solution_value > 0.5
    assigned_tail = None
    for t in tail_ids:
        if (t, f) in x and x[t, f].solution_value > 0.5:
            assigned_tail = t
            break
    actual_dep = int(round(dep[f].solution_value))
    orig_tail = FLIGHTS[f]["orig_tail"]
    results.append({
        "flight": f,
        "origin": FLIGHTS[f]["origin"],
        "dest": FLIGHTS[f]["dest"],
        "sched_dep": FLIGHTS[f]["sched_dep"],
        "actual_dep": actual_dep if not is_cancelled else None,
        "delay_min": (actual_dep - FLIGHTS[f]["sched_dep"]) if not is_cancelled else 0,
        "orig_tail": orig_tail,
        "assigned_tail": assigned_tail,
        "swapped": (assigned_tail != orig_tail) if assigned_tail else False,
        "cancelled": is_cancelled,
    })

results.sort(key=lambda r: r["sched_dep"])

summary = {
    "total_delay_min": sum(r["delay_min"] for r in results),
    "num_delayed": sum(1 for r in results if r["delay_min"] > 0),
    "num_cancelled": sum(1 for r in results if r["cancelled"]),
    "num_swapped": sum(1 for r in results if r["swapped"]),
    "objective_value": mdl.objective_value,
    "solve_status": str(mdl.solve_details.status),
    "num_variables": mdl.number_of_variables,
    "num_constraints": mdl.number_of_constraints,
}

output = {
    "flights": results,
    "summary": summary,
    "disruption": {
        "tail": DISRUPTED_TAIL,
        "location": "ORD",
        "blackout_start": BLACKOUT_START,
        "blackout_end": BLACKOUT_END,
    },
    "weights": {"delay": W_DELAY, "cancel": W_CANCEL, "swap": W_SWAP},
}

with open("result.json", "w") as fh:
    json.dump(output, fh, indent=2)

print(json.dumps(summary, indent=2))
