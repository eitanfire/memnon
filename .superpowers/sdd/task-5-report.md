# Task 5 Report: BoulderJS Packet Adapter and Optional CLI Bridge

## Status

DONE

## Scope

Implemented only the task-owned adapter-layer files:

- `src/orchestration/boulderjs.py`
- `schemas/boulderjs-recap-packet.schema.json`
- `tests/test_orchestration_boulderjs.py`

No engine wiring was added.

## TDD Record

1. Added `tests/test_orchestration_boulderjs.py` first.
2. Ran:

```bash
python3 -m unittest discover -s tests -p 'test_orchestration_boulderjs.py' -v
```

3. Verified red state:
   - `ModuleNotFoundError: No module named 'src.orchestration.boulderjs'`
4. Added the minimal adapter implementation and packet schema.
5. Re-ran the same unittest command and verified green state.

## What Was Implemented

### Adapter

Added `write_boulderjs_recap_packet(job, event, analysis, config) -> ArtifactBundle` in `src/orchestration/boulderjs.py`.

Behavior:

- Creates packet directory at `<boulderjs_dir>/<source_event_id>`
- Writes:
  - `event.json`
  - `talk.json`
  - `abstract.txt`
  - `thoughts.txt`
  - `source-links.json`
- Returns an `ArtifactBundle` for `boulderjs_recap_packet`

Packet details use the exact task-brief defaults:

- event number from `default_boulderjs_event_number`
- talk number from `default_boulderjs_talk_number`
- location set to `galvanize`
- talk label set to `Talk: Feature 🎦`

### Optional CLI Bridge

Added `build_social_agent_command(packet_dir: Path, config: dict[str, Any]) -> list[str] | None`.

Behavior:

- Returns `None` unless `run_social_agent_cli` is truthy
- Returns `None` if default event/talk numbers are missing
- Otherwise returns:
  - `npm run draft --`
  - `--event=<number>`
  - `--talk=<number>`
  - `--post-type=recap`
  - `--talk-abstract-file=<packet_dir>/abstract.txt`
  - `--thought-file=<packet_dir>/thoughts.txt`

### Schema

Added `schemas/boulderjs-recap-packet.schema.json` to encode the binding packet contract:

- `event.json`
- `talk.json`
- `abstract.txt`
- `thoughts.txt`
- `source-links.json`

## Tests Added

`tests/test_orchestration_boulderjs.py`

- Verifies packet writer creates the five required files
- Verifies the optional CLI bridge command starts with `["npm", "run", "draft", "--"]`
- Verifies the schema contains the required packet members and keeps `source-links.json` as an array contract

## Verification

Command:

```bash
python3 -m unittest discover -s tests -p 'test_orchestration_boulderjs.py' -v
```

Result:

- 2 tests run
- 0 failures
- exit code 0

## Repo Context / Constraints Observed

- Left unrelated untracked `.superpowers` files untouched
- Kept verification scoped to the Task 5 unittest file, per brief
- Did not attempt full-suite verification because the repo baseline is known to be incomplete due to optional dependencies such as `firebase_admin`

## Concerns

None at the adapter layer.
