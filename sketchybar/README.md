# SketchyBar

Install or update the symlinks with `stow -t ~ sketchybar`, then run
`sketchybar --reload`. AeroSpace uses the matching configuration in `aerospace/`.

## Workspace updates

- AeroSpace workspace/focus events request an immediate shared snapshot.
- A two-second poll catches window opens/closes and any events skipped while a
  refresh was already running. Do not subscribe the sync job to
  `space_windows_change`: moving windows during workspace changes can generate
  bursts of these native events.
- `aerospace_sync.py` sends only changed properties. Identical snapshots make no
  SketchyBar write calls, and changing focus does not reload all workspace icons.
- A 300 ms cooldown also collapses sequential workspace/focus events; no sync
  loop keeps running in response to its own updates.
- The cache is invalidated on every bar configuration reload, so newly created
  items always receive a full initial update.

## Bounded scripts

`run_once.py` gives slow/querying jobs separate, nonblocking OS locks and deadlines
for their entire process trees. Overlapping requests are discarded, not queued;
periodic jobs retry on the next tick. Lock files are deliberately never deleted.
The worker inherits the lock, preventing overlap even if its supervisor is killed.
Timeouts and normal termination also stop the worker's descendants.
Successful jobs are never sent cleanup signals. During cancellation the group
leader is reaped only after signaling, preventing its PID from being reused in
between signals. Cancellation failures and deadlines are timestamped in the
existing SketchyBar error log; this does not install a background recorder.

The volume event handler stays lightweight and unguarded so rapid key presses do
not discard the final volume value. Media has a periodic fallback for skipped
playback events. Bluetooth queries have both a five-second profiler deadline and
an eight-second whole-job deadline, rather than the profiler's 180-second default.

The historical SketchyBar log contains process/file exhaustion failures from an
older configuration. Those logs alone do not establish the cause of every macOS
freeze; these safeguards prevent event amplification and unbounded script overlap.

Run the regression tests without touching the live bar:

```sh
python3 -B -m unittest discover -s sketchybar/tests -v
aerospace reload-config --dry-run --no-gui --warnings-as-errors
```
