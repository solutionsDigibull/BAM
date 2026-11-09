// Every pinned action button uses one of four words (BuLLM action-button rule, 17 Oct 2026). Its hover/focus tooltip
// copy lives here only, keyed by screen ID and word. `disabled` replaces the gate reason on the tooltip's second line;
// `running` is the second line while the button is busy.
import { screenLabel } from '../tracker/trackerConfig.ts'

export type ActionWord = 'Start' | 'Approve' | 'Edit' | 'Retry'

const DEFAULTS: Record<ActionWord, string> = {
  Start: "I'll start this step and run it to the end.",
  Approve: "I'll accept this result and move on to the next step.",
  Edit: "I'll let you change this result before you approve it.",
  Retry: "I'll run this step again after the failure.",
}

type Copy = { tip?: string; disabled?: string; running?: string }
export const ACTION_TIPS: Record<string, Partial<Record<ActionWord, Copy>>> = {
  '1.1': { Start: { tip: "I'll start ingestion. I'll check, standardize and parse your files, then take you to Review." } },
  '1.8': { Approve: { tip: "I'll accept the original file as it is. I won't change it, and I'll check it again." } },
  '1.4': {
    Edit: { tip: "I'll let you fix or remove files and change their classification before approving." },
    Approve: { tip: "I'll confirm the document set and start the Context stage." },
  },
  '2.5': { Start: { tip: "I'll sync the latest Admin Masters, then build the knowledge. I'll ask you to check it on Verify." } },
  '2.4': { Start: { tip: "I'll generate the statement of work from this context, then open Scope." } },
  '2.3': {
    Start: { tip: "I'll test the context with the sample questions before you approve it.', running: 'I'm testing the sample questions against the context…" },
    Approve: { tip: "I'll accept the verified context and start the Workbench stage." },
    // The failed-answer detail's actions.
    Edit: { tip: "I'll take you to the master to fix the data behind this answer." },
    Retry: { tip: "I'll ask this question again after the fix.', running: 'I'm asking the question again…" },
  },
  '3.1': {
    Edit: { tip: "I'll let you change the statement of work before you approve it." },
    Approve: { tip: "I'll approve the statement of work. I'll build the skills from it in the next steps.', running: 'I'm approving the statement of work…" },
  },
  // ponytail: "macro-skills, sub-skills" from the prompt reads Agent > Skill here (terminology rule in CLAUDE.md).
  '3.2': {
    Edit: { tip: "I'll let you change agents, skills and their columns in the grid." },
    Approve: { tip: "I'll approve the grid. I'll ask you to confirm before the checks run.', running: 'I'm starting the checks…" },
    // The confirm popup's Start.
    Start: { tip: "I'll run the contract, dependency and translation checks. I won't save anything yet." },
  },
  '4.2': {
    Start: { tip: "I'll run the contract, dependency and translation checks on the reviewed grid.', running: 'I'm starting the checks…" },
    Edit: { tip: "I'll let you change the generated manifest before approving." },
    Approve: { tip: "I'll accept the manifest and open Translate." },
    Retry: { tip: "I'll run the Template checks again. I'll keep your earlier inputs.', running: 'I'm running the checks again…" },
  },
  '4.3': {
    Edit: { tip: "I'll let you change the generated code before approving." },
    Approve: { tip: "I'll accept the generated skills and open Validation.', running: 'I'm approving the generated skills…" },
  },
  '4.8': {
    Start: { tip: "I'll set up an isolated test environment for the generated skills. Nothing touches production.', running: 'I'm setting up the sandbox…" },
    Approve: { tip: "I'll accept the sandbox and open Check." },
  },
  '4.4': {
    Start: { tip: "I'll run the checks on the skills in the sandbox.', running: 'I'm running the checks…" },
    Approve: { tip: "I'll accept the check results and open Certify.', disabled: 'Available once all checks pass." },
  },
  '4.5': { Approve: { tip: "I'll sign off on the results and open Commit.', disabled: 'Available once all checks pass.', running: 'I'm signing off…" } },
  // ponytail: "Skill Palette" from the prompt reads Staging (no Skill Palette exists; CLAUDE.md "No hot-reload / Skill Palette").
  '4.6': { Approve: { tip: "I'll save the certified skills and make them available on Staging. I'll ask you to confirm first.', disabled: 'Available once the skills are certified.', running: 'I'm saving the certified skills and going live…" } },
}

/** Props for GateButton / Tip: the tooltip text, the disabled-reason override and the running text. */
export function tips(screen: string, word: ActionWord) {
  const c = ACTION_TIPS[screen]?.[word]
  const tip = c?.tip ?? (word === 'Retry' ? `I'll run ${screenLabel(screen)} again. I'll keep your earlier inputs.` : DEFAULTS[word])
  return { tip, tipReason: c?.disabled, tipBusy: c?.running ?? `I'm running ${screenLabel(screen)}…` }
}
