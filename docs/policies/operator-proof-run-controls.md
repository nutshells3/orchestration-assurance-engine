# Operator Proof Run Controls

Managed proof jobs exist to stop runaway search from monopolizing the workbench.

## Use `cancel` first

Use `cancel` when:

- the run is still plausibly recoverable
- you want the worker to stop at the next control point
- you want to preserve the distinction between a requested stop and a forced kill

If the process does not stop within `cancel_grace_seconds`, the governor escalates to forced termination and records `cancel_grace_exceeded`.

## Use `kill` when:

- the search is clearly runaway
- the process stopped responding to cancel
- the machine is resource constrained and immediate stop is required

`kill` is forceful and should be treated as an operational incident, not a normal review outcome.

## Interpret terminal reasons

- `completed`: build ended normally
- `cancelled`: operator stop request completed without escalation
- `cancel_grace_exceeded`: operator requested stop, but the process had to be force-killed
- `kill_requested`: explicit hard kill
- `wall_timeout`: absolute budget exhausted
- `idle_timeout`: stdout/stderr stopped moving for too long
- `build_failed`: Isabelle exited non-zero without a governor stop
- `worker_failed`: the run worker itself failed

## Review boundary

- A stopped proof job does not change promotion state by itself.
- Cancel, kill, timeout, and forced termination are operational facts that must remain visible in the review journal or release smoke output.
- Operators may retry a run, but retries do not bypass audit or promotion checkpoints.
- Suspicious or unstable theories should remain below their recommended gate until a reviewer records the rationale for any exception.
