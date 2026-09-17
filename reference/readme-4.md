What this is, factually: solved with your licensed CPLEX engine's API (docplex/CPLEX Community Edition in my sandbox, code unchanged for your license). All schedule data is synthetic, built by me for this demo, not real airline data. Model solved to proven optimality: 226 variables, 544 constraints.

Scenario: 5 tails (3× subtype A, 2× subtype B), 20 flights, 4 airports (ORD hub). Tail A2 grounded at ORD 09:35–13:35. The MIP re-solves tail-to-flight assignment, sequencing, and departure times, minimizing weighted delay + cancellations + swaps.

Result: A2's two stranded flights are swapped to tail A3, which had scheduled slack (a mid-day layover I built into A3's original rotation) to absorb them with only 20 min delay each. Zero cancellations. Full breakdown, chronology, and swap highlighting in the dashboard.

Two bugs I caught and fixed before showing you this: my first two attempts at the sequencing constraints let a tail be assigned to two overlapping flights at once (physically impossible). I found this by manually checking the output against real turn-time math, not by design — flagging so you know to sanity-check outputs on any model like this, mine included.

My own choices, not sourced from anything you gave me: the objective weights (1/min delay, 150/swap, 2000/cancellation) and the tail-rotation structure, including A3's layover. Change either and the answer changes — worth noting to a customer if they push on "why this swap."