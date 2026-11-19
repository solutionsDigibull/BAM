import { Fragment, useEffect, useState } from "react";
import { useCreate, useList } from "@refinedev/core";
import {
  IconAlertTriangle, IconBolt, IconCheck, IconCircleCheckFilled, IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarLeftExpand, IconLock, IconMoon, IconPlayerPlay, IconSend, IconSun, IconX,
} from "@tabler/icons-react";
import { WORKFLOW } from "./workflow.generated";
import type { Skill, SubSkill } from "./types";
import { ICONS, ModeIcon } from "./icons";
import { StepForm, emptyValues, toArguments, type Values } from "./StepForm";
import { Outputs, type Result } from "./Outputs";
import { ServerError } from "./mcp";
import bullHead from "./assets/bull-head.png";

type Phase = "idle" | "running" | "done" | "error" | "forbidden";
interface Run { phase: Phase; result?: Result; check?: Result; message?: string }
interface Msg { who: "ai" | "me"; text: string }
type Pick = { s: number; sub: number | "review" };

// The assistant answers from the Workflow's own description (skills, steps, schemas). It calls no model.
function answer(question: string, skill: Skill, sub: SubSkill | null): string {
  const q = question.toLowerCase();
  if (!sub) return `${skill.name} ends with a human review. ${skill.review.note}`;
  if (/input|need|field|provide/.test(q)) {
    return sub.inputs.length ? `${sub.name} needs: ${sub.inputs.map(f => f.label + (f.required ? " (required)" : "")).join(", ")}.` : `${sub.name} needs no input.`;
  }
  if (/output|result|produce|return/.test(q)) {
    return sub.outputs.length ? `${sub.name} returns: ${sub.outputs.map(f => f.label).join(", ")}.` : `${sub.name} returns a result record.`;
  }
  if (/check|verify|accept/.test(q)) return sub.steps.filter(s => s.mode === "auto").slice(-1)[0]?.name ?? "The check runs after the action.";
  return `${sub.name}: ${sub.summary}`;
}

export default function App() {
  const { result, query } = useList<Skill>({ resource: "skills" });
  const { mutateAsync } = useCreate();
  const skills: Skill[] = (result?.data as Skill[] | undefined) ?? [];
  const [dark, setDark] = useState(() => new URLSearchParams(location.search).get("theme") === "dark");
  const [pick, setPick] = useState<Pick>({ s: 0, sub: 0 });
  const [railMini, setRailMini] = useState(false);
  const [assistantHidden, setAssistantHidden] = useState(false);
  const [values, setValues] = useState<Record<string, Values>>({});
  const [runs, setRuns] = useState<Record<string, Run>>({});
  const [approved, setApproved] = useState<Record<string, boolean>>({});
  const [msgs, setMsgs] = useState<Msg[]>([{ who: "ai", text: `Hello. This is ${WORKFLOW.name}. Pick a skill on the left; ask me what a step needs or returns.` }]);
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => { document.documentElement.setAttribute("data-theme", dark ? "dark" : "light"); }, [dark]);

  const skill = skills[pick.s];
  const sub = skill && pick.sub !== "review" ? skill.subs[pick.sub] : null;
  const runOf = (id: string): Run => runs[id] ?? { phase: "idle" };
  const doneIn = (k: Skill) => k.subs.every(x => runOf(x.id).phase === "done");

  const say = (...more: Msg[]) => setMsgs(m => [...m, ...more]);

  async function run(target: SubSkill) {
    const { args, problems } = toArguments(target.inputs, values[target.id] ?? emptyValues(target.inputs));
    if (problems.length) { setRuns(r => ({ ...r, [target.id]: { phase: "error", message: problems.join("; ") } })); return; }
    setRuns(r => ({ ...r, [target.id]: { phase: "running" } }));
    try {
      const action = await mutateAsync({ resource: "tools", values: { name: target.actionTool, arguments: args } });
      const check = await mutateAsync({ resource: "tools", values: { name: target.checkTool, arguments: args } });
      setRuns(r => ({ ...r, [target.id]: { phase: "done", result: action.data as Result, check: check.data as Result } }));
      say({ who: "ai", text: `${target.name} finished and its check ran.` });
    } catch (e) {
      const forbidden = e instanceof ServerError && e.forbidden;
      setRuns(r => ({ ...r, [target.id]: { phase: forbidden ? "forbidden" : "error", message: e instanceof Error ? e.message : "The step failed" } }));
    }
  }

  function approve() {
    if (!skill) return;
    setApproved(a => ({ ...a, [skill.id]: true }));
    say({ who: "ai", text: `${skill.name} approved.${pick.s + 1 < skills.length ? ` Starting ${skills[pick.s + 1].name}.` : " The workflow is complete."}` });
    if (pick.s + 1 < skills.length) setPick({ s: pick.s + 1, sub: 0 });
  }

  function sendBack() {
    if (!skill) return;
    setRuns(r => { const next = { ...r }; for (const x of skill.subs) delete next[x.id]; return next; });
    say({ who: "me", text: `Sent back: ${note || "no note"}` });
    setNote(null);
    setPick({ s: pick.s, sub: 0 });
  }

  function ask() {
    if (!draft.trim() || !skill) return;
    say({ who: "me", text: draft }, { who: "ai", text: answer(draft, skill, sub) });
    setDraft("");
  }

  const phaseOf = (k: Skill, i: number) => (approved[k.id] ? "done" : i === pick.s ? "active" : "wait");

  return (
    <div className="app">
      <header className="top">
        <div className="scr-t"><h1>{WORKFLOW.name}</h1><p>Workflow {WORKFLOW.version}</p></div>
        <div className="top-right">
          <button className="iconbtn" onClick={() => setDark(!dark)} aria-label="Toggle theme">{dark ? <IconSun size={16} /> : <IconMoon size={16} />}</button>
        </div>
      </header>

      <div className={"main" + (assistantHidden ? " assistant-collapsed" : "")}>
        <section className={"pane rail" + (railMini ? " mini" : "")} data-pane="rail" aria-label="Skills">
          <div className="rail-h">
            {!railMini && <h2>Skills</h2>}
            <button className="iconbtn sm" onClick={() => setRailMini(!railMini)} aria-expanded={!railMini} aria-label={railMini ? "Expand navigation" : "Collapse navigation"}>
              {railMini ? <IconLayoutSidebarLeftExpand size={14} /> : <IconLayoutSidebarLeftCollapse size={14} />}
            </button>
          </div>
          <div className="pane-b">
            {query.isLoading && <p className="muted" role="status">Loading skills...</p>}
            {query.isError && <p className="state err" role="alert"><IconAlertTriangle size={14} /> The skills could not be loaded.</p>}
            {!query.isLoading && !query.isError && skills.length === 0 && <p className="muted">This workflow has no skills yet.</p>}
            <div className="stages">
              {skills.map((k, i) => {
                const Icon = ICONS[k.icon] ?? IconBolt;
                const st = phaseOf(k, i);
                return (
                  <div key={k.id} className="stage-li">
                    {i < skills.length - 1 && <span className="st-line" aria-hidden />}
                    <button className={"skill " + st} onClick={() => setPick({ s: i, sub: 0 })} aria-current={i === pick.s} title={k.name}>
                      <div className="dot">{st === "done" ? <IconCheck size={15} /> : <Icon size={15} />}</div>
                      {!railMini && <div className="tx"><div className="nm">{k.name}</div><div className="st">{st === "done" ? "Approved" : st === "active" ? `${k.subs.filter(x => runOf(x.id).phase === "done").length} of ${k.subs.length} sub-skills done` : "Waiting"}</div></div>}
                    </button>
                    {!railMini && i === pick.s && (
                      <div className="subs">
                        {k.subs.map((x, j) => (
                          <button key={x.id} className={"sub-row" + (runOf(x.id).phase === "done" ? " done" : pick.sub === j ? " act" : "")} onClick={() => setPick({ s: i, sub: j })}>
                            <span className="sd" />{x.name}
                            <span className="mode">{runOf(x.id).phase === "done" && <span className="done-tick"><IconCircleCheckFilled size={14} /></span>}<span className="mode-icon"><ModeIcon mode="auto" size={22} /></span></span>
                          </button>
                        ))}
                        <button className={"sub-row" + (pick.sub === "review" ? " act" : "")} onClick={() => setPick({ s: i, sub: "review" })}>
                          <span className="sd" />{k.review.name}
                          <span className="mode"><span className="mode-icon"><ModeIcon mode="human" size={14} /></span></span>
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </section>

        <section className={"pane mid"} data-pane="assistant" aria-label="Assistant">
          {assistantHidden ? (
            <button className="assistant-show" onClick={() => setAssistantHidden(false)} aria-expanded={false} aria-label="Show assistant"><IconLayoutSidebarLeftExpand size={16} /><span>Show assistant</span></button>
          ) : (
            <>
              <div className="pane-h">
                <h2 className="assistant-heading">Assistant</h2>
                <button className="iconbtn sm assistant-hide" onClick={() => setAssistantHidden(true)} aria-expanded={true} aria-label="Hide assistant"><IconLayoutSidebarLeftCollapse size={14} /></button>
              </div>
              <div className="pane-b">
                {msgs.length === 1 && <div className="chat-empty"><span className="ce-bull"><img src={bullHead} alt="" /></span></div>}
                {msgs.map((m, i) => (
                  <Fragment key={i}><div className={"msg " + m.who}><div><div className="who">{m.who === "ai" ? "Assistant" : "You"}</div><div className="b">{m.text}</div></div></div></Fragment>
                ))}
              </div>
              <div className="composer">
                <input className="in" value={draft} placeholder="Ask about this step" aria-label="Ask the assistant" onChange={e => setDraft(e.target.value)} onKeyDown={e => e.key === "Enter" && ask()} />
                <button className="iconbtn" onClick={ask} aria-label="Send"><IconSend size={16} /></button>
              </div>
            </>
          )}
        </section>

        <section className="pane right" data-pane="steps" aria-label="Steps and output">
          <div className="pane-h"><h2>{sub ? sub.name : skill ? skill.review.name : "Steps"}</h2>{skill && <span className="sub">{skill.name}</span>}</div>
          <div className="pane-b">
            {!skill && !query.isLoading && <p className="muted">Nothing to run yet.</p>}
            {skill && sub && (() => {
              const r = runOf(sub.id);
              const stepState = (k: number) => {
                if (r.phase === "done") return "done";
                if (r.phase === "running") return k === 0 ? "done" : "run";
                return k === 0 ? "run" : "wait";
              };
              return (
                <>
                  <p className="muted">{sub.summary}</p>
                  {sub.steps.map((stp, k) => {
                    const st = stepState(k);
                    return (
                      <div key={stp.name + k} className={"step" + (st === "done" ? " done" : st === "run" ? " run" : "")}>
                        <div className="si"><ModeIcon mode={stp.mode} size={stp.mode === "human" ? 16 : 20} /></div>
                        <div className="st-t"><div className="st-n">{stp.name}</div><div className="st-m">{stp.note}</div></div>
                        {st === "done" ? <span className="done-tick"><IconCircleCheckFilled size={20} /></span> : <span className={"chip " + st}>{st === "run" ? (r.phase === "running" ? "Running" : "Next") : "Queued"}</span>}
                      </div>
                    );
                  })}
                  <div className="ocard">
                    <h3>Input</h3>
                    <StepForm fields={sub.inputs} values={values[sub.id] ?? emptyValues(sub.inputs)} dark={dark} disabled={r.phase === "running"}
                      onChange={v => setValues(all => ({ ...all, [sub.id]: v }))} />
                  </div>
                  {r.phase === "running" && <p className="state" role="status">Running {sub.name}...</p>}
                  {r.phase === "error" && <p className="state err" role="alert"><IconAlertTriangle size={14} /> {r.message}</p>}
                  {r.phase === "forbidden" && <p className="state err" role="alert"><IconLock size={14} /> You do not have access to run this step.</p>}
                  {r.phase === "done" && r.result && <Outputs fields={sub.outputs} result={r.result} dark={dark} />}
                  {r.phase === "done" && r.check && (
                    <div className="ocard"><h3>Check</h3>{Object.entries(r.check).map(([k, v]) => <div className="kv" key={k}><span className="k">{k.replace(/_/g, " ")}</span><span className="v">{typeof v === "object" ? JSON.stringify(v) : String(v)}</span></div>)}</div>
                  )}
                  {r.phase === "idle" && <p className="muted">No result yet. Fill the input and press Run.</p>}
                </>
              );
            })()}
            {skill && pick.sub === "review" && (
              <>
                <p className="muted">{skill.review.note}</p>
                {skill.subs.map(x => (
                  <div className="step" key={x.id}>
                    <div className="si"><ModeIcon mode="auto" size={20} /></div>
                    <div className="st-t"><div className="st-n">{x.name}</div><div className="st-m">{runOf(x.id).phase === "done" ? "Done" : "Not run yet"}</div></div>
                    {runOf(x.id).phase === "done" ? <span className="done-tick"><IconCircleCheckFilled size={20} /></span> : <span className="chip wait">Queued</span>}
                  </div>
                ))}
                {note !== null && <textarea className="reject-note" rows={2} placeholder="Note for the rework: what should change" value={note} onChange={e => setNote(e.target.value)} aria-label="Note for the rework" />}
              </>
            )}
          </div>
          {skill && sub && (
            <div className="actionbar">
              <button className="abtn" disabled={runOf(sub.id).phase === "running"} onClick={() => void run(sub)}><IconPlayerPlay size={16} /> Run {sub.name}</button>
            </div>
          )}
          {skill && pick.sub === "review" && (
            <div className="actionbar">
              <div className="hk">{doneIn(skill) ? "Every sub-skill is done. Nothing moves on until you approve." : "Run every sub-skill before approving."}</div>
              <div className="rrow">
                <button className="rbtn" disabled={!doneIn(skill) || approved[skill.id]} onClick={approve}><IconCheck size={16} /> {approved[skill.id] ? "Approved" : "Approve"}</button>
                {note === null ? <button className="rbtn ghost" onClick={() => setNote("")}><IconX size={16} /> Send back with note</button> : <button className="rbtn ghost" onClick={sendBack}><IconX size={16} /> Send back</button>}
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
