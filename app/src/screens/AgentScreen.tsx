// Agent: describe the room in plain words; the lights agent (hub/agentsvc.py) sets
// the look, then Undo or save it as a lite scene. All of it lives in State.agent.
import { ActionsPanel } from "../components/agent/ActionsPanel";
import { LookPanel } from "../components/agent/LookPanel";
import { PromptPanel } from "../components/agent/PromptPanel";
import { ReplyPanel } from "../components/agent/ReplyPanel";

export function AgentScreen() {
  return (
    <div data-testid="agent-screen">
      <PromptPanel />
      <ReplyPanel />
      <LookPanel />
      <ActionsPanel />
    </div>
  );
}
