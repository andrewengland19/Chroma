/* eslint-disable */
// GENERATED from contracts/*.schema.json by scripts/gen-types.mjs — do not edit.
// Run `npm run gen:types` after changing a schema.

/**
 * The only way to change console state (BUILD_ORDER §5.2). Identical for UI, board, replay and CLI. `src` is stamped by the receiver; senders omit it. Unknown intents are rejected.
 */
export type Intent =
  | FaderIntent
  | FlashIntent
  | AssignIntent
  | BankIntent
  | FaderFnIntent
  | HueIntent
  | SatIntent
  | PaletteApplyIntent
  | PaletteStoreIntent
  | CueRecallIntent
  | CueStoreIntent
  | BlackoutIntent
  | WhoIntent;
/**
 * Acts on last_touched. Exactly one of delta (encoder detents) or v (degrees).
 */
export type HueIntent = {
  [k: string]: unknown;
} & {
  intent: "hue";
  delta?: number;
  /**
   * Degrees in [0, 360).
   */
  v?: number;
  src?: "ui" | "board" | "replay" | "cli";
};
/**
 * Acts on last_touched. Exactly one of delta (encoder detents) or v (0..1).
 */
export type SatIntent = {
  [k: string]: unknown;
} & {
  intent: "sat";
  delta?: number;
  v?: number;
  src?: "ui" | "board" | "replay" | "cli";
};
/**
 * UI/CLI -> chromad over the Unix socket, one JSON object per line (BUILD_ORDER §5.1). Every request carries an id; the response echoes it.
 */
export type IpcRequest =
  | {
      id: number;
      op: "intent";
      intent: Intent;
    }
  | {
      id: number;
      op: "get_state";
    }
  | {
      id: number;
      op: "subscribe";
    }
  | {
      id: number;
      op: "refresh";
    }
  | {
      id: number;
      op: "set_art_mode";
      mode: "det" | "paint" | "ai";
    }
  | {
      id: number;
      op: "fixtures.list";
    }
  | {
      id: number;
      op: "fixtures.discover";
    }
  | {
      id: number;
      op: "fixtures.upsert";
      fixture: Fixture;
    }
  | {
      id: number;
      op: "fixtures.remove";
      fixture_id: string;
    }
  | {
      id: number;
      op: "fixtures.test";
      fixture_id: string;
    }
  | {
      id: number;
      op: "groups.list";
    }
  | {
      id: number;
      op: "groups.upsert";
      group: Group;
    }
  | {
      id: number;
      op: "groups.remove";
      group_id: string;
    }
  | {
      id: number;
      op: "spaces.upsert";
      /**
       * Without id: a new Space (its id is made from the name). With id: rename it.
       */
      space: {
        id?: string;
        name: string;
      };
    }
  | {
      id: number;
      op: "spaces.remove";
      space_id: string;
    }
  | {
      id: number;
      op: "banks.get";
    }
  | {
      id: number;
      op: "banks.set";
      bank: "A" | "B" | "C";
      channel: number;
      group_id: string | null;
    }
  | {
      id: number;
      op: "cues.rename";
      /**
       * Palette swatch or cue index.
       */
      i: number;
      name: string;
    }
  | {
      id: number;
      op: "takes.list";
    }
  | {
      id: number;
      op: "takes.get";
      take_id: string;
    }
  | {
      id: number;
      op: "takes.delete";
      take_id: string;
    }
  | {
      id: number;
      op: "takes.rename";
      take_id: string;
      name: string;
    }
  | {
      id: number;
      op: "takes.export";
      take_id: string;
    }
  | {
      id: number;
      op: "takes.duplicate";
      take_id: string;
    }
  | {
      id: number;
      op: "takes.set_flag";
      take_id: string;
      flag: "training";
      value: boolean;
    }
  | {
      id: number;
      op: "takes.marker.add";
      take_id: string;
      p_ms: number;
      label: string;
    }
  | {
      id: number;
      op: "takes.marker.remove";
      take_id: string;
      p_ms: number;
      label?: string;
    }
  | {
      id: number;
      op: "rec.arm";
      take_id?: string;
      mode?: "replace" | "overdub" | "merge";
    }
  | {
      id: number;
      op: "rec.start";
      take_id?: string;
      mode?: "replace" | "overdub" | "merge";
    }
  | {
      id: number;
      op: "rec.stop";
    }
  | {
      id: number;
      op: "play.start";
      take_id: string;
      pos_ms?: number;
      loop?: boolean;
    }
  | {
      id: number;
      op: "play.stop";
    }
  | {
      id: number;
      op: "play.seek";
      pos_ms: number;
    }
  | {
      id: number;
      op: "take.nudge";
      take_id: string;
      ms: number;
    }
  | {
      id: number;
      op: "board.pairing";
    }
  | {
      id: number;
      op: "groups.set_limits";
      group_id: string;
      max_level?: number;
      max_flash_hz?: number;
      idle_dim_min?: number | null;
    }
  | {
      id: number;
      op: "banks.swap";
      from: {
        bank: "A" | "B" | "C";
        channel: number;
      };
      to: {
        bank: "A" | "B" | "C";
        channel: number;
      };
    }
  | {
      id: number;
      op: "agent.ask";
      prompt: string;
      /**
       * Space ids the agent may touch; empty or missing = living-room only.
       *
       * @maxItems 32
       */
      spaces?: string[];
    }
  | {
      id: number;
      op: "agent.undo";
    }
  | {
      id: number;
      op: "agent.save";
      name: string;
    }
  | {
      id: number;
      op: "scenes.list";
    }
  | {
      id: number;
      op: "scenes.recall";
      name: string;
    };
/**
 * chromad -> client reply to a request. Echoes `id`. Failures are {ok:false, error}. Successful replies carry op-specific fields (see $defs for the shapes).
 */
export type IpcResponse = IpcOk | IpcError;
/**
 * chromad -> subscribed clients. `state` right after subscribe (and after a resync), then `patch` (RFC 7386, coalesced <= 30 Hz); `toast` for user-visible messages.
 */
export type IpcEvent = StateEvent | PatchEvent | ToastEvent;
/**
 * @minItems 8
 * @maxItems 8
 */
export type BankRow = (string | null)[];

export interface ChromaContracts {
  state?: State;
  intent?: Intent;
  ipcRequest?: IpcRequest;
  ipcResponse?: IpcResponse;
  ipcEvent?: IpcEvent;
  takeHeader?: TakeHeader;
  takeEvent?: TakeEvent;
  fixturesFile?: FixturesFile;
  consoleFile?: ConsoleFile;
  takesList?: TakesList;
  takeFull?: TakeFull;
  takeExport?: TakeExport;
}
/**
 * Full console/engine state sent by chromad (BUILD_ORDER §5.1). Patches are RFC 7386 merge patches against it.
 */
export interface State {
  now: NowPlayingState;
  /**
   * WHO DRIVES. auto == Engine.enabled true; board == false.
   */
  who: "auto" | "board";
  art_mode: "det" | "paint" | "ai";
  bank: "A" | "B" | "C";
  fader_fn: "level" | "hue" | "sat";
  master: number;
  blackout: boolean;
  /**
   * 3 banks x 8, ordered A1..A8, B1..B8, C1..C8.
   *
   * @minItems 24
   * @maxItems 24
   */
  channels: ChannelState[];
  last_touched: ChannelRef | null;
  room: RoomFixture[];
  /**
   * @minItems 8
   * @maxItems 8
   */
  cues: CueSummary[];
  cue_active: number | null;
  /**
   * @minItems 8
   * @maxItems 8
   */
  palette: string[];
  rec: RecState;
  play: PlayState;
  health: Health;
  /**
   * Bumps when fixtures/groups/banks change; the UI refetches them.
   */
  fixtures_rev: number;
  /**
   * Bumps when the take library changes; the UI refetches takes.list.
   */
  takes_rev: number;
  activity: Activity;
  agent: AgentState;
}
export interface NowPlayingState {
  /**
   * NowPlaying.track_id(); empty when nothing is known.
   */
  track_id: string;
  title: string;
  artist: string;
  album: string;
  playing: boolean;
  /**
   * SongClock position at the time of the patch; published at <= 4 Hz, the UI interpolates while playing.
   */
  pos_ms: number | null;
  dur_ms: number | null;
  /**
   * Opaque URL for the current artwork (chroma-art://current?rev=N in the app).
   */
  art_url: string | null;
}
export interface ChannelState {
  bank: "A" | "B" | "C";
  ch: number;
  group_id: string | null;
  level: number;
  /**
   * Degrees in [0, 360).
   */
  hue: number;
  sat: number;
  flash: boolean;
  /**
   * Pickup state for the physical board; false shows a ghost marker.
   */
  engaged: boolean;
}
export interface ChannelRef {
  bank: "A" | "B" | "C";
  ch: number;
}
export interface RoomFixture {
  fixture_id: string;
  label: string;
  x: number;
  y: number;
  /**
   * Current output colour (brightness applied); null if unknown.
   */
  hex: string | null;
  online?: boolean;
}
export interface CueSummary {
  /**
   * Palette swatch or cue index.
   */
  id: number;
  name: string;
  stored: boolean;
}
export interface RecState {
  state: "idle" | "armed" | "recording";
  take_id: string | null;
  elapsed_ms: number;
  events: number;
  mode: "replace" | "overdub" | "merge";
  name: string;
  groups_touched: number;
}
export interface PlayState {
  state: "idle" | "playing";
  take_id: string | null;
  pos_ms: number;
  loop: boolean;
}
export interface Health {
  atv: "up" | "down";
  lifx: {
    found: number;
    total: number;
  };
  /**
   * none = never paired; virtual = board_sim is the source; online/offline = physical board.
   */
  board: "none" | "virtual" | "online" | "offline";
}
/**
 * Console activity strip (gui-1 bottom): one lane per group with event spans of the take being recorded or played (song ms).
 */
export interface Activity {
  source: "rec" | "play" | null;
  dur_ms: number | null;
  /**
   * @maxItems 24
   */
  lanes: {
    group_id: string;
    color: string;
    /**
     * @maxItems 200
     */
    spans: number[][];
  }[];
}
/**
 * Agent tab: the plain-language lights agent (hub/lights_agent.py), run as a background job by agent.ask. idle = never asked since chromad started.
 */
export interface AgentState {
  status: "idle" | "running" | "done" | "error";
  /**
   * Why agent ops are refused right now (e.g. a fake engine), or null when the agent can run.
   */
  blocked: string | null;
  prompt: string;
  reply: string;
  suggested_name: string | null;
  /**
   * @maxItems 50
   */
  actions: {
    tool: string;
    summary: string;
  }[];
  /**
   * Bulb states read back after the run (or undo), not the engine's idea of the room.
   *
   * @maxItems 64
   */
  look: {
    label: string;
    power: "on" | "off" | "unreachable";
    hex: string | null;
    brightness: number | null;
  }[];
  error: string | null;
  can_undo: boolean;
  saved_as: string | null;
}
/**
 * Raw fader position. Board-sourced values go through pickup (§5.3).
 */
export interface FaderIntent {
  intent: "fader";
  ch: number | "master";
  v: number;
  src?: "ui" | "board" | "replay" | "cli";
}
/**
 * Momentary full level while down; returns to the fader level on release.
 */
export interface FlashIntent {
  intent: "flash";
  ch: number;
  down: boolean;
  src?: "ui" | "board" | "replay" | "cli";
}
/**
 * Remap a channel of the current bank. Without group_id it only selects the channel (sets last_touched); group_id null clears the binding.
 */
export interface AssignIntent {
  intent: "assign";
  ch: number;
  group_id?: string | null;
  src?: "ui" | "board" | "replay" | "cli";
}
export interface BankIntent {
  intent: "bank";
  bank: "A" | "B" | "C";
  src?: "ui" | "board" | "replay" | "cli";
}
export interface FaderFnIntent {
  intent: "fader_fn";
  fn: "level" | "hue" | "sat";
  src?: "ui" | "board" | "replay" | "cli";
}
export interface PaletteApplyIntent {
  intent: "palette_apply";
  /**
   * Palette swatch or cue index.
   */
  i: number;
  src?: "ui" | "board" | "replay" | "cli";
}
export interface PaletteStoreIntent {
  intent: "palette_store";
  /**
   * Palette swatch or cue index.
   */
  i: number;
  src?: "ui" | "board" | "replay" | "cli";
}
export interface CueRecallIntent {
  intent: "cue_recall";
  /**
   * Palette swatch or cue index.
   */
  i: number;
  src?: "ui" | "board" | "replay" | "cli";
}
export interface CueStoreIntent {
  intent: "cue_store";
  /**
   * Palette swatch or cue index.
   */
  i: number;
  src?: "ui" | "board" | "replay" | "cli";
}
export interface BlackoutIntent {
  intent: "blackout";
  on: boolean;
  src?: "ui" | "board" | "replay" | "cli";
}
export interface WhoIntent {
  intent: "who";
  who: "auto" | "board";
  src?: "ui" | "board" | "replay" | "cli";
}
export interface Fixture {
  id: string;
  label: string;
  type: "lifx" | "virtual" | "wled";
  /**
   * active = driven; not_added = discovered but not in the rig; planned = placeholder hardware (no output).
   */
  status: "active" | "not_added" | "planned";
  /**
   * slow = LIFX washes (~100–250 ms); fast = WLED / virtual.
   */
  class: "slow" | "fast";
  ip?: string;
  mac?: string;
  /**
   * LIFX group label from discovery (e.g. Living Room).
   */
  lifx_group?: string;
  /**
   * Optional display grouping, e.g. 'Playbill 01–12' collapses into one table row.
   */
  series?: string;
  x: number;
  y: number;
  /**
   * Display colour for the Setup table dot.
   */
  color?: string;
  /**
   * The Space (room) this fixture is in; missing = living-room.
   */
  space?: string;
}
export interface Group {
  id: string;
  /**
   * Scribble-strip label, e.g. LR ALL.
   */
  name: string;
  /**
   * UI colour (lanes, bank grid).
   */
  color: string;
  members: string[];
  /**
   * Safety cap applied after master; flash cannot exceed it.
   */
  max_level: number;
  /**
   * Safety: on/off cycles per second; never above 3 (BUILD_ORDER §8.1).
   */
  max_flash_hz: number;
  /**
   * BOARD only: dim to 30 % after this many minutes without input; null = off.
   */
  idle_dim_min: number | null;
}
export interface IpcOk {
  id: number;
  ok: true;
  state?: State;
}
export interface IpcError {
  id: number | null;
  ok: false;
  error: string;
}
export interface StateEvent {
  ev: "state";
  state: State;
}
export interface PatchEvent {
  ev: "patch";
  patch: {};
}
export interface ToastEvent {
  ev: "toast";
  level: "info" | "warn" | "error";
  text: string;
}
/**
 * First line of ~/.chroma/takes/<id>_<slug>.jsonl (BUILD_ORDER §5.4). Rewritten atomically when the take changes.
 */
export interface TakeHeader {
  v: 1;
  id: string;
  /**
   * e.g. 'Older — draft 03'.
   */
  name: string;
  track_id: string;
  title: string;
  artist: string;
  album: string;
  dur_ms: number | null;
  fixtures_rev: number;
  nudge_ms: number;
  /**
   * Include in the AI training set.
   */
  training: boolean;
  markers: Marker[];
  /**
   * Wall-clock ms when recording started.
   */
  created_ms?: number;
  performer?: string;
  /**
   * Dominant album-art colour when recording started (library thumbnail).
   */
  art_hex?: string | null;
  /**
   * Largest SongClock resync correction seen while recording.
   */
  sync_err_ms?: number | null;
  init?: TakeInit;
  /**
   * Group names/colours at record time, keyed by group id (for lanes).
   */
  groups?: {
    [k: string]: {
      name: string;
      color: string;
    };
  };
  stats?: {
    events: number;
    groups_used: string[];
    length_ms: number;
  };
}
export interface Marker {
  p: number;
  label: string;
}
/**
 * Console snapshot when recording started (replay seeds from it).
 */
export interface TakeInit {
  bank: "A" | "B" | "C";
  fader_fn: "level" | "hue" | "sat";
  master: number;
  blackout: boolean;
  /**
   * @minItems 24
   * @maxItems 24
   */
  channels: {
    group_id: string | null;
    level: number;
    /**
     * Degrees in [0, 360).
     */
    hue: number;
    sat: number;
  }[];
}
/**
 * One recorded intent (every line after the header). `i` is the intent exactly as received (post-pickup), without src. b/c/a/v are the resolved target so replay does not depend on the live bank, fader function or last-touched channel.
 */
export interface TakeEvent {
  /**
   * Song position (SongClock) in ms.
   */
  p: number;
  /**
   * Wall clock ms.
   */
  w: number;
  i: Intent;
  /**
   * Resolved group id (informational).
   */
  g?: string | null;
  b?: "A" | "B" | "C";
  c?: number | "master";
  a?:
    | "level"
    | "hue"
    | "sat"
    | "flash"
    | "assign"
    | "master"
    | "blackout"
    | "cue"
    | "palette"
    | "bank"
    | "fader_fn"
    | "who"
    | "select";
  v?: number | boolean | string | null;
}
/**
 * ~/.chroma/fixtures.json — fixtures, groups, the 3x8 bank map and the Spaces (rooms). `rev` bumps on every change and is stamped into takes as fixtures_rev.
 */
export interface FixturesFile {
  v: 1;
  rev: number;
  fixtures: Fixture[];
  groups: Group[];
  banks: Banks;
  /**
   * Rooms of the house; each fixture lives in one. living-room is the default and always present (files from before Spaces are migrated from the bulbs' LIFX groups).
   */
  spaces?: Space[];
}
export interface Banks {
  A: BankRow;
  B: BankRow;
  C: BankRow;
}
export interface Space {
  id: string;
  /**
   * Shown on the Room panel's tabs and the Agent's checkboxes, e.g. Bedroom.
   */
  name: string;
}
/**
 * ~/.chroma/console.json — persisted console: channel values, palette, cues, last bank / fader function, playback offset.
 */
export interface ConsoleFile {
  v: 1;
  bank: "A" | "B" | "C";
  fader_fn: "level" | "hue" | "sat";
  master: number;
  /**
   * @minItems 24
   * @maxItems 24
   */
  channels: LookChannel[];
  /**
   * @minItems 8
   * @maxItems 8
   */
  palette: string[];
  /**
   * @minItems 8
   * @maxItems 8
   */
  cues: Cue[];
  /**
   * Global replay offset added to every take's nudge.
   */
  play_offset_ms?: number;
}
export interface LookChannel {
  level: number;
  /**
   * Degrees in [0, 360).
   */
  hue: number;
  sat: number;
}
export interface Cue {
  name: string;
  fade_ms?: number;
  /**
   * Snapshot of all 24 channels (not master / who / bank); null = empty.
   */
  look: LookChannel[] | null;
}
export interface TakesList {
  takes: TakeSummary[];
}
export interface TakeSummary {
  id: string;
  name: string;
  title: string;
  artist: string;
  album: string;
  dur_ms: number | null;
  created_ms: number;
  events: number;
  groups_used: string[];
  training: boolean;
  track_id?: string;
  art_hex?: string | null;
}
export interface TakeFull {
  header: TakeHeader;
  events: TakeEvent[];
}
export interface TakeExport {
  jsonl: string;
  filename: string;
}

export type Channel = ChannelState;
export type BankName = State["bank"];
export type FaderFn = State["fader_fn"];
export type Who = State["who"];
export type ArtMode = State["art_mode"];
export type RecordMode = RecState["mode"];
