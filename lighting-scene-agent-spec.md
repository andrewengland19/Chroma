<!--
  Document:      Lighting Scene Agent — Product & Technical Specification
  Author:        Andrew England / andrewengland19
  Created:       2026-10-17
  Last updated:  2026-10-17 14:05 EDT
-->

# Lighting Scene Agent — Specification (v0.1)

Working name: **Scene Agent**. This document specifies a new layer that sits beside Chroma and the `lite` command. It does not specify, assume, or require any change to either of them.

## 1. Purpose, scope, and ground rules

Scene Agent lets me describe how I want my living room to feel in plain language ("a rainy-evening feel, but keep the TV side dim and cooler") and turns that into a spatially aware light scene: different fixtures get different colors and brightness depending on where they are in the room and what they illuminate. It starts from the scenes I already use (for example `lite scene vibes1`), so new scenes inherit my taste instead of being generic.

I am dividing the work between a language model and ordinary code because each is good at a different thing. The model interprets vague intent and expresses it in room terms ("pool of warm light at the seating area, TV wall deep teal"). Deterministic code does everything that has to be exact and repeatable: reading the room model, computing the color and brightness for each fixture, validating safety limits, and talking to the lights. A scene the model proposed can then be previewed, reproduced, saved, and diffed, and a misbehaving model cannot send arbitrary commands to hardware.

### 1.1 Hard constraints (the "no-touch" contract with Chroma)

1. Scene Agent makes **no changes to Chroma**: no edits to its code, configuration, scene files, state, or processes, and no files written into any Chroma directory.
2. This specification makes **no claims about how Chroma works internally**. Where Scene Agent needs to know something about Chroma or `lite`, it is listed in §3 as an *assumption to verify*, and the design tolerates the assumption being wrong by failing safely or degrading to a smaller feature set.
3. Scene Agent's only contract with the existing system is the one I described: the `lite` executable is on `PATH`, and `lite scene <name>` is a way to apply a named scene. Everything else is discovered at runtime or configured by me.
4. Scenes Scene Agent creates live in Scene Agent's own storage under its own namespace (`agent.*`). It never overwrites, renames, or deletes any `lite` scene.

### 1.2 In scope for v1

- Living room only, with a room model that can later hold more rooms.
- Importing my existing `lite` scenes as reference material and as starting points.
- Natural-language scene creation and modification ("like vibes1 but...").
- Spatial reasoning over a user-authored room model.
- Preview before apply, undo after apply, and saving of generated scenes.
- A command-line interface, plus a thin wrapper so it can be invoked from Claude Code.

### 1.3 Out of scope for v1

Whole-home scenes; music or non-light devices; voice input; automatic triggering from calendar, presence, or time; image- or camera-based room mapping; and any form of Scene Agent replacing, wrapping, or reconfiguring Chroma. The integration points in §12 show where those could attach later.

## 2. Architecture overview

```
 natural language request
          |
          v
 +-----------------+      reads       +----------------------+
 |  Agent (LLM)    |<---------------->| Scene Registry       |  imported lite scenes,
 |  interprets     |   tools          | + Taste Profile      |  snapshots, tags
 +--------+--------+                  +----------+-----------+
          | SceneSpec (JSON, schema-validated)   ^
          v                                      | capture / import
 +-----------------+      reads       +----------+-----------+
 |  Renderer       |<-----------------| Room Model           |  fixtures, zones,
 |  (deterministic)|                  | (YAML, user-owned)   |  anchors, aim
 +--------+--------+                  +----------------------+
          | per-fixture color/brightness table
          v
 +-----------------+   preview (terminal + HTML floor plan)
 |  Safety Gate    |-----------------------------------------> me: confirm / adjust
 +--------+--------+
          | approved table
          v
 +-----------------+--------------------+
 | Output Driver                        |
 |  A. lite passthrough (recall only)   |--> `lite scene <name>`   (existing scenes)
 |  B. direct LAN driver (new scenes)   |--> bulbs over local network
 |  C. file export (optional, §12)      |--> for me to adopt manually, if ever
 +--------------------------------------+
```

Because the language model only ever produces a **SceneSpec** (§6) and has no direct line to hardware, the failure modes are bounded: the worst a bad model response can do is produce a scene I decline at the preview step.

## 3. Interface assumptions and how the design handles being wrong

The following are assumptions I have not verified. The first milestone (§14, M0) is a short discovery pass that confirms or corrects each one, and the table says what happens if it is wrong.

| # | Assumption | If wrong |
|---|---|---|
| A1 | `lite` is an executable on `PATH` that Scene Agent can run as a subprocess | Scene Agent refuses to start the lite-dependent features and reports which executable it looked for; configurable path override |
| A2 | `lite scene <name>` applies the named scene and exits `0` on success and non-zero on failure | The `lite passthrough` driver treats exit status as unreliable and verifies by reading bulb state back (§7.3) |
| A3 | Scene names are single tokens matching a simple pattern such as `[A-Za-z0-9_-]+` | Name validation is configurable by regex in config; names that do not match are never passed to a process |
| A4 | The bulbs are reachable over the local network and can be read and written without going through `lite` | Driver B and the capture method in §5.2 are unavailable; Scene Agent falls back to recall-only mode (apply existing `lite` scenes by name) |
| A5 | There is no documented way to list or dump `lite` scenes | Not required: names come from a list I maintain or from a listing command if one turns out to exist (§5.1) |
| A6 | Chroma may be running and may also send commands to the bulbs at the same time | Scene Agent warns when it detects bulb state changing after its own write (§7.4) and offers to reapply; it never tries to stop or control Chroma |

Scene Agent invokes `lite` as an argument vector with no shell involved, so a scene name can never be interpreted as shell syntax. Scene names must be present in the Scene Registry (§5) before they are passed to the executable, which prevents the model from running an arbitrary string as a command.

## 4. Room Model

The room model is a YAML file I author and own. It is the single source of spatial truth. I am keeping it as an explicit, human-readable file rather than inferring layout, because a wrong inferred position silently produces wrong scenes, whereas a wrong value in a file I wrote is something I can see and fix.

### 4.1 Contents

- **Coordinate system:** meters, right-handed, origin at a floor corner I choose; `z` is height. Dimensions of the room are recorded so the preview can draw it.
- **Viewpoint convention:** words like "left", "right", "behind", and "in front" are interpreted relative to a person sitting at a named anchor (`main_seat`) facing another named anchor (`focal_wall`). The convention is stored in the file so it is never ambiguous.
- **Anchors:** named points or surfaces with positions, for example the main seat, the TV wall, the window, the entry, a reading chair, the corner behind the sofa.
- **Zones:** named regions defined as boxes or as "within radius R of an anchor", for example `seating`, `tv_wall`, `window_side`, `entry`, `ceiling`.
- **Fixtures:** one entry per physical light.

### 4.2 Fixture fields

| Field | Meaning |
|---|---|
| `id` | A stable name I choose, used everywhere in specs and previews |
| `device` | How to find the physical light on the network (label or identifier as reported by the bulb); filled in by the calibration flow (§4.4) |
| `position` | `[x, y, z]` in meters |
| `aim` | Where its light mainly lands: a direction vector, or the name of a surface or anchor (`up`, `tv_wall`, `seating`), because a bulb in an uplight lights the ceiling and wall, not the floor beneath it |
| `role` | `ambient`, `accent`, or `task`, used to guide the model (accent fixtures can carry saturated color; task fixtures stay near white) |
| `capability` | `single`, `multizone`, or `matrix`, taken from discovery rather than typed by hand (§4.3) |
| `zones` | Optional per-zone offsets for multizone and matrix fixtures (position along the strip or tile), so a strip can show a gradient across the room |
| `limits` | Optional per-fixture caps (maximum brightness, allowed color range), for example a fixture near the sofa that should never go above a given level |

An illustrative excerpt (values are placeholders, not my actual room):

```yaml
room: living_room
units: meters
viewpoint: { from: main_seat, facing: tv_wall }
dimensions: { x: 5.0, y: 4.2, z: 2.6 }

anchors:
  main_seat: { position: [2.5, 0.8, 1.0] }
  tv_wall:   { surface: { plane: "y=4.2" } }
  window:    { surface: { plane: "x=0.0" } }

zones:
  seating:     { within: { anchor: main_seat, radius: 1.8 } }
  tv_side:     { within: { anchor: tv_wall, radius: 2.0 } }
  window_side: { within: { anchor: window, radius: 2.0 } }

fixtures:
  - { id: lamp_left,  position: [0.6, 3.6, 0.9], aim: up,       role: ambient }
  - { id: lamp_right, position: [4.4, 3.6, 0.9], aim: up,       role: ambient }
  - { id: strip_tv,   position: [2.5, 4.1, 0.6], aim: tv_wall,  role: accent  }
```

### 4.3 Capability discovery

At startup the driver asks each reachable light what it is (single color, multizone strip, tile matrix), its supported color-temperature range, and whether it supports color at all. Capabilities are stored as discovered, not typed, so the renderer never asks a white-only bulb for teal and never assumes a Kelvin range a model does not have. The color-temperature range differs by model, so I treat it as a per-device value.

### 4.4 Calibration flow

To avoid hand-typing coordinates for every light, Scene Agent offers a guided flow: it flashes one light at a time at low brightness and asks me to say where it is ("on the floor by the left of the TV", "on the shelf above the window"). The model proposes coordinates and an aim from my description relative to the named anchors, shows them on the floor plan, and writes them to the room file only after I confirm. The flow is optional and the file stays hand-editable.

## 5. Scene Registry and Taste Profile

The registry is Scene Agent's read-only knowledge of my existing `lite` scenes, plus the scenes it has made itself.

### 5.1 Learning which `lite` scenes exist

Scene Agent treats `lite` as a black box and supports these sources in order, using the first that works:

1. **A listing command**, if M0 discovers that `lite` has one.
2. **A user-maintained list** in Scene Agent's own config: scene names, plus optional one-line notes ("warm, low, evening").
3. **Read-only import** from a path I configure, if the scene definitions turn out to be readable files. Scene Agent opens them read-only and never writes there.

### 5.2 Capturing what a scene actually looks like

Knowing a scene's name tells the agent nothing about its colors. Rather than depend on how `lite` stores scenes, I use **capture**, which relies only on the contract in §1.1:

1. Record the current state of every fixture (so it can be restored).
2. Run `lite scene <name>` through the passthrough driver.
3. Wait for the lights to settle (configurable delay, longer if a scene uses slow transitions).
4. Read each fixture's state back from the bulb: color, brightness, Kelvin, and per-zone values for strips and tiles.
5. Store that as a **snapshot** for the scene, tagged with the capture time.
6. Restore the original state.

I chose capture over parsing scene files because it measures what the lights really do, it keeps working if the scene format changes, and it needs no knowledge of `lite` internals. The costs are that the room visibly cycles through the scenes during capture and that animated scenes cannot be captured as one frame. For the first, capture runs only when I invoke it, with a confirmation, one scene at a time or in a batch. For the second, the snapshot is marked `static: false` when state changes between two reads, and such scenes are available by name for recall but are not used as bases for derived scenes.

### 5.3 Normalization

Each snapshot is converted into the room model's terms: a table from fixture `id` to color and brightness, plus derived descriptors computed by code (average brightness, average and range of color temperature, dominant hue family, left-right and front-back brightness balance, contrast between zones). These descriptors are what let me say "like vibes1" and have it mean something concrete.

### 5.4 Taste Profile

From the normalized snapshots Scene Agent builds a small **Taste Profile**: the brightness range I actually use, my preferred color-temperature range for evening scenes, the hues that recur, and how much contrast between zones I typically leave. The profile is injected into the model's context as plain statistics and used as soft defaults, so that "relaxing" by default sits inside my range rather than the model's idea of relaxing. It is generated, inspectable, and editable, and I can switch it off per request ("ignore my usual style").

### 5.5 Agent-made scenes

Scenes the agent creates are stored as SceneSpecs with provenance: the original request, the base scene if any, the model used, and the time. They are named `agent.<slug>` and are listed alongside imported scenes but visibly separated.

## 6. SceneSpec: the model's output format

The model never emits raw device commands. It emits a **SceneSpec**, a small JSON document validated against a schema, which describes the scene in room terms. Anything that fails validation is sent back to the model with the error and is never rendered.

```json
{
  "name": "agent.rainy_evening",
  "base": { "scene": "vibes1", "mode": "start_from" },
  "summary": "Warm low pool at the seating area, TV wall darker and cooler, ceiling dim.",
  "layers": [
    { "op": "scale", "target": { "zone": "tv_side" },
      "brightness_factor": 0.4 },
    { "op": "shift", "target": { "zone": "tv_side" },
      "kelvin_delta": 600 },
    { "op": "pool", "center": "main_seat", "radius_m": 2.0,
      "color": { "kelvin": 2200 }, "brightness": 0.30, "falloff": "smooth" },
    { "op": "gradient", "from": "window_side", "to": "tv_side",
      "colors": [ { "hue_deg": 200, "sat": 0.35 }, { "hue_deg": 190, "sat": 0.6 } ],
      "brightness": 0.2, "targets": { "role": "accent" } }
  ],
  "transition_s": 3.0
}
```

### 6.1 Operations

Operations are applied in order. Each acts on a **target** (a fixture `id`, a named zone, a role, `all`, or a predicate such as `height > 2.0` or `aim == up`) and each fixture's contribution is weighted by where it is.

| Op | Effect | Spatial behavior |
|---|---|---|
| `start_from` (in `base`) | Begins from a captured snapshot instead of from darkness | Per-fixture values copied by `id` |
| `fill` | Sets a color and brightness uniformly on the target | None, applies fully to everything targeted |
| `pool` | A soft region of light around a point or anchor | Weight falls off with distance from `center` using `smooth`, `linear`, or `hard` falloff |
| `gradient` | Blends between two colors from one place to another | Weight is the fixture's normalized position along the line between `from` and `to`, with multizone strips sampled per zone |
| `wash` | Colors a surface | Applies to fixtures whose `aim` lands on that surface |
| `scale` | Multiplies brightness | Weighted by target |
| `shift` | Adds to hue, saturation, or Kelvin | Weighted by target |
| `accent` | A single small bright point of color | Applies to the nearest fixture(s) to a position |

### 6.2 Why this vocabulary

I picked operations that correspond to how people actually describe light in a room (a pool of light, a wash on the wall, brighter toward the window) so the model can express intent compactly and a human reading the spec can understand it. A flat list of per-bulb values would be easier to generate but impossible to adapt: if I add a lamp or move one, a spatial spec still renders sensibly, while a per-bulb list would not.

### 6.3 Relative edits

Requests like "like vibes1 but cooler and less bright near the TV" are expressed as `start_from` plus a few `shift` and `scale` layers. Expressing the change as a small diff against a known snapshot keeps the result close to the original instead of the model re-imagining it from scratch, and makes "undo the cooler part" a matter of removing one layer.

## 7. Renderer, Safety Gate, and Output Drivers

### 7.1 Renderer (deterministic)

For each fixture the renderer starts from the base snapshot (or from off), then applies each layer in order: it computes the layer's weight for that fixture from its position or aim, and blends the layer's result into the current value. The renderer is a pure function of (room model, SceneSpec, snapshots), so the same inputs always give the same output, which is what makes testing and saved scenes meaningful.

I will blend colors in a perceptual color space (OKLab or similar) and convert to the device's hue, saturation, brightness, and Kelvin representation only at the end. I am doing this because interpolating directly in hue-saturation-brightness can sweep through unintended hues and produces uneven apparent brightness along a gradient. White light is kept as a Kelvin value with zero saturation rather than approximated with RGB, because that is how the bulbs render whites best. Colors a fixture cannot produce are clamped to the nearest producible value, and the preview marks any fixture where clamping changed the result noticeably.

Multizone strips and tile matrices are rendered per zone from each zone's own position, so a gradient across a strip is a real gradient and not one color per strip.

### 7.2 Safety Gate

Applied after rendering and before any output, and not overridable by the model:

- **Brightness ceilings** per fixture and a global ceiling, with an optional lower ceiling during configured night hours.
- **Flicker protection:** no generated animation may alternate faster than a configured rate (default: no more than 3 changes per second, which is the commonly used photosensitivity threshold), and v1 generates static scenes with timed transitions only.
- **Rate limiting:** writes to any one device are limited to well under the roughly 20 messages per second per device that the LAN protocol recommends, with transitions delegated to the bulb's own fade where possible rather than streamed from the computer.
- **Transition floor:** a minimum fade time so that a large brightness change is never an abrupt jump unless I explicitly ask.
- **Allowlist:** only fixtures listed in the room model are ever addressed.

### 7.3 Output Drivers

The Output Driver is an interface with interchangeable implementations. Each reports its capabilities so the rest of the system can degrade gracefully.

**Driver A: `lite passthrough` (recall).** Runs `lite scene <name>` for scenes in the registry. This is how existing scenes are applied, and it is the only way Scene Agent causes anything to change through `lite`. It verifies by reading the lights back where possible, and reports a mismatch rather than assuming success.

**Driver B: direct LAN (new scenes).** Writes the rendered table to the bulbs over the local network. This is what applies an `agent.*` scene, because by constraint there is no way to add one to `lite`. It reads each fixture's prior state first so that **Revert** can restore it.

**Driver C: file export (optional).** Writes a scene file in Scene Agent's own format to Scene Agent's own directory, and nothing else. It exists so that if I ever decide to teach Chroma to import agent scenes, there is a stable artifact to import. That decision and any code it needs are entirely mine and are not part of this spec (§12).

### 7.4 Coexistence with Chroma

Chroma and Scene Agent may both address the same bulbs, and the bulbs simply obey the most recent command. Scene Agent therefore does three things and no more: it records the state it expects after applying a scene; it re-reads a few fixtures a short time later and tells me if something else has changed them; and it offers "reapply". It does not detect, pause, or control Chroma. I am keeping this passive because acting on another program I have said not to touch would violate the contract in §1.1, and because for a single-user living room, a plain warning is the right level of machinery.

## 8. Agent behavior

### 8.1 Tools available to the model

The model works through a small set of tools implemented by Scene Agent. None of them touch hardware directly.

| Tool | Purpose |
|---|---|
| `list_scenes()` | Names and short descriptors of imported and agent-made scenes |
| `describe_scene(name)` | The normalized snapshot and derived descriptors for one scene |
| `get_room()` | The room model: anchors, zones, fixtures with roles and capabilities |
| `get_taste_profile()` | The generated statistics from §5.4 |
| `propose_scene(spec)` | Validates a SceneSpec, renders it, and returns the per-fixture table plus any warnings (clamping, ceilings reached, unknown targets) |
| `preview(spec)` | Produces the terminal and HTML preview for me |
| `save_scene(spec)` | Stores it as `agent.<name>` (only after I have confirmed) |

Applying a scene, saving it, and reverting are deliberately **not** tools the model can call on its own: they are actions I take from the CLI after seeing the preview. The model proposes, and I decide.

### 8.2 Request handling

1. The agent classifies the request: new scene, modification of a named scene, spatial adjustment of the current state ("make it a bit warmer by the window"), or recall ("vibes1").
2. For recall, it names the scene and the system uses Driver A with no model reasoning needed beyond name matching.
3. For everything else, it gathers context with the tools above, then calls `propose_scene`. If the tool returns warnings or errors (an unknown zone, a fixture that cannot do color, a brightness above the ceiling), the model gets one automatic chance to correct the spec and explains what it changed.
4. It presents the preview with a one-sentence summary and the layer list in plain language.
5. I respond with **apply**, **adjust** (another instruction, which revises the same spec rather than starting over), **save**, or **cancel**.

### 8.3 Handling ambiguity

The agent asks a clarifying question only when the answer materially changes the result and cannot be inferred from the room model, such as an undefined anchor ("by the bookshelf" when no bookshelf exists in the room file) or a conflict between two instructions. Otherwise it proceeds with stated assumptions that I can overturn at the preview. At most one question is asked before showing a first proposal, so the interaction does not turn into an interview.

### 8.4 Latency

For live use, the interaction should feel quick. The target is a preview within a few seconds for a typical request on a fast model, with the model name configurable. Recall of an existing scene involves no model call at all.

## 9. Preview

Seeing the result before the lights change is what makes this safe to trust, and a spatial scene is hard to judge from numbers alone, so the preview has two parts:

1. **Terminal summary:** a table of fixture, role, zone, color swatch (using terminal color), brightness percentage, and Kelvin or hue, plus the warnings.
2. **Floor-plan view:** a generated local HTML file (opened on request) that draws the room from above using the room model, with each fixture as a glowing circle in its rendered color and a soft halo showing approximate reach based on `aim`. A toggle switches to a side view so height bands are visible. This is an approximation for judging layout and balance, not a photometric simulation, and the preview says so.

The same renderer output drives both the preview and the real write, so what I approve is what is applied.

## 10. Interfaces

### 10.1 Command line

```
scene-agent ask "<natural language request>"     # propose + preview, then prompt
scene-agent apply [--yes]                        # apply the last approved proposal
scene-agent revert                               # restore the state before the last apply
scene-agent save <name>                          # store the last proposal as agent.<name>
scene-agent recall <lite-scene-name>             # Driver A, no model call
scene-agent capture [<name> | --all]            # snapshot existing lite scenes (§5.2)
scene-agent calibrate                            # guided fixture placement (§4.4)
scene-agent room show|edit                       # print or open the room model
scene-agent history                              # recent proposals and applies
```

`--dry-run` is the default behavior of `ask`: nothing reaches the lights until `apply`. A config option can enable auto-apply for trivial adjustments, off by default.

### 10.2 Claude Code

Because I already work with Claude Code in a repo that knows my room, a short instructions file or slash command in Scene Agent's own repo can describe the CLI so that Claude Code can call `scene-agent ask` and `scene-agent apply` on my behalf. The CLI remains the only path to the lights, so the same preview and safety gate apply however it is invoked.

## 11. Configuration, storage, and secrets

```
~/.scene-agent/                 (location configurable)
  config.yaml                   LLM model, lite path, night-hours ceilings, timeouts
  room/living_room.yaml         the room model (§4)
  registry/
    scenes.yaml                 known lite scene names + notes (§5.1)
    snapshots/<scene>.json      captured states (§5.2)
    taste_profile.json          generated (§5.4)
  scenes/agent.<slug>.json      saved SceneSpecs with provenance (§5.5)
  history/                      proposals, renders, apply/revert records
  .env                          LLM API key (gitignored)
```

Scene Agent's code lives in its own repository or directory, separate from Chroma's. The LLM API key is read from an environment variable or a gitignored `.env` file and never written to config, snapshots, history, or logs, since my repositories are public. The room model and snapshots reveal the layout of my home, so the default `.gitignore` excludes `room/`, `registry/`, and `history/`, and any example files committed to a public repo use placeholder values.

## 12. Integration points for later (not part of v1)

These show where Scene Agent could connect to other things without changing its design:

- The **physical lighting board** can fire `scene-agent recall <name>` or `scene-agent apply` from its bridge process, so a button press maps to a scene with no language model in the path.
- A **home server** deployment can run the same CLI behind a small local API, and Home Assistant or a voice assistant can call it. A "shower time" automation would simply recall a saved scene.
- **Time-aware defaults** (matching color temperature to the sun's position, computed locally from latitude, longitude, and date) could be added as a deterministic input to the renderer without any model involvement.

### Appendix: optional requests to the Chroma side (for my own later consideration)

Scene Agent works without any of the following. They are listed only so that if I later choose to improve the integration, the options are written down in one place. None is assumed, and none is a dependency.

- A way for `lite` to list its scenes, or to print a scene's definition, which would replace capture (§5.2) for static scenes.
- A way for Chroma to import a scene file from a directory, which would let Driver C export become a first-class path.
- A way for Chroma to signal that it is currently running an effect, which would let Scene Agent warn instead of detecting a changed state after the fact.

## 13. Testing and evaluation

- **Renderer unit tests:** given a fixed room model and SceneSpec, the output table is exactly as expected (golden files). This covers each operation, falloff shapes, layer ordering, clamping, and multizone sampling.
- **Schema tests:** malformed or hostile SceneSpecs (unknown fixture, brightness over the ceiling, an operation name that does not exist, a target that tries to name a command) are rejected before rendering.
- **Fake devices:** a simulated driver and a stub `lite` executable let the whole pipeline run without hardware, including capture, apply, revert, and coexistence warnings.
- **Prompt evaluation set:** about 30 representative requests, each with *property* assertions on the rendered output instead of exact colors, for example "the TV-side fixtures average lower brightness than the seating fixtures", "the result stays within my Taste Profile's brightness range", "no fixture exceeds the night ceiling", "fixtures that cannot do color receive only white values". Properties are checked automatically on every change to the prompt, model, or schema, so regressions in the language step are caught as quickly as regressions in code.
- **Hardware smoke test:** a script that applies a known spec and reads the lights back to confirm they match within tolerance.

## 14. Acceptance criteria

1. With the stub `lite` and fake devices, `scene-agent ask` for a plain-language request produces a schema-valid SceneSpec, a per-fixture table, and a preview, and changes nothing until `apply`.
2. `scene-agent recall <name>` for a scene in the registry runs `lite scene <name>` as an argument vector and reports success or failure; a name not in the registry is refused without starting a process.
3. `capture` of a static scene stores a snapshot, restores the previous lights state afterward, and marks an animated scene as non-static.
4. A request like "like vibes1 but cooler and dimmer near the TV" yields a spec whose first layer is `start_from vibes1`, and the rendered TV-side fixtures are cooler and dimmer than in the snapshot while the others are unchanged.
5. Changing a fixture's position in the room model changes the rendered result for spatial operations without any edit to the SceneSpec.
6. `revert` restores the prior state of every fixture after an apply.
7. No file inside any Chroma directory is created, modified, or deleted at any point (verified by a filesystem check in the test suite against a configured Chroma path and by code review of the write paths).
8. No secret appears in the repository, snapshots, history, or logs.

## 15. Milestones

1. **M0, discovery (short):** confirm or correct assumptions A1 to A6 by trying `lite` from a terminal and noting what it does; decide the scene-listing source in §5.1; record the result in this document.
2. **M1, foundations:** room model schema and loader, SceneSpec schema and validator, renderer with golden tests, fake devices and stub `lite`.
3. **M2, recall and capture:** Driver A, registry, capture and normalization, Taste Profile generation.
4. **M3, the agent:** tool set, `ask` flow, preview (terminal and floor plan), `apply`, `revert`, `save`.
5. **M4, spatial polish:** calibration flow, multizone and matrix rendering, night-hour ceilings, coexistence warnings.
6. **M5, integrations:** Claude Code wrapper, evaluation set, packaging for the home server, hooks for the lighting board.

## 16. Open questions

1. **How should new scenes be applied long term?** The spec uses a direct driver for `agent.*` scenes because the contract forbids changing Chroma. If I later want them to appear in `lite`, that is a Chroma-side decision (Appendix) and this layer would only need Driver C.
2. **Which lights are in the living room, and what are they?** The room model needs their types and rough positions. The calibration flow can build this, but it needs the bulbs to be reachable.
3. **Are any existing scenes animated or time-based?** Capture handles only static frames; animated scenes will be recallable but not usable as bases.
4. **How aggressive should the Taste Profile be?** Strong defaults make results feel like mine, but can make the agent timid. A per-request override and a strength setting are in the design; the default needs tuning against real scenes.
5. **Which model for the language step?** A fast, inexpensive model may be enough because the output is a small structured document and the code does the exact work; a stronger one may interpret abstract moods better. It is a config value, so it can be compared on the evaluation set in §13.
6. **Is the LAN protocol access to the bulbs acceptable alongside Chroma?** This is a question of preference, not capability; if I prefer that all hardware writes stay inside Chroma, the layer degrades to recall-only plus Driver C export, and generated scenes stay as files until Chroma can read them.
