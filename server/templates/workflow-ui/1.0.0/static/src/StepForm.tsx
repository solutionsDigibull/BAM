// The input form of one sub-skill, built from the fields the generator took from the tool's input
// schema. Required fields, enums and types come from the schema, never from this file.
import type { Field } from "./types";
import { Intake, type Picked } from "./Intake";

export type Values = Record<string, unknown>;

export function emptyValues(fields: Field[]): Values {
  const values: Values = {};
  for (const f of fields) values[f.name] = f.widget === "checkbox" ? false : "";
  return values;
}

// Turns the form state into the tool's arguments; returns the problems when it cannot.
export function toArguments(fields: Field[], values: Values): { args: Values; problems: string[] } {
  const args: Values = {};
  const problems: string[] = [];
  for (const f of fields) {
    const raw = values[f.name];
    const blank = raw === "" || raw === undefined || (Array.isArray(raw) && raw.length === 0);
    if (blank && f.widget !== "checkbox") {
      if (f.required) problems.push(`${f.label} is required`);
      continue;
    }
    if (f.widget === "number") {
      const n = Number(raw);
      if (Number.isNaN(n) || (f.type === "integer" && !Number.isInteger(n))) problems.push(`${f.label} must be ${f.type === "integer" ? "a whole number" : "a number"}`);
      else args[f.name] = n;
    } else if (f.widget === "json") {
      try { args[f.name] = JSON.parse(String(raw)); } catch { problems.push(`${f.label} must be valid JSON`); }
    } else {
      args[f.name] = raw;
    }
  }
  return { args, problems };
}

export function StepForm({ fields, values, onChange, dark, disabled }: {
  fields: Field[]; values: Values; onChange: (v: Values) => void; dark: boolean; disabled: boolean;
}) {
  const set = (name: string, value: unknown) => onChange({ ...values, [name]: value });
  const pick = (name: string) => (files: Picked[]) => set(name, files.length ? JSON.stringify(files) : "");
  if (fields.length === 0) return <p className="muted">This step needs no input.</p>;
  return (
    <form className="sform" onSubmit={e => e.preventDefault()} aria-label="Step input">
      {fields.map(f => {
        const id = `f-${f.name}`;
        const label = <label htmlFor={id}>{f.label}{f.required && <span className="req" aria-hidden="true"> *</span>}</label>;
        const hint = f.description ? <span className="hint">{f.description}</span> : null;
        const value = values[f.name];
        let control;
        if (f.widget === "checkbox") control = <input id={id} type="checkbox" checked={value === true} disabled={disabled} onChange={e => set(f.name, e.target.checked)} />;
        else if (f.widget === "select") control = (
          <select id={id} value={String(value ?? "")} disabled={disabled} onChange={e => set(f.name, e.target.value)}>
            <option value="">Choose...</option>
            {(f.enum ?? []).map(o => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
          </select>
        );
        else if (f.widget === "file") control = <Intake id={`${id}-intake`} dark={dark} onPick={pick(f.name)} />;
        else if (f.widget === "textarea" || f.widget === "json") control = (
          <textarea id={id} rows={f.widget === "json" ? 4 : 3} value={String(value ?? "")} disabled={disabled} spellCheck={false} onChange={e => set(f.name, e.target.value)} />
        );
        else control = <input id={id} type={f.widget === "number" ? "number" : "text"} value={String(value ?? "")} disabled={disabled} onChange={e => set(f.name, e.target.value)} />;
        return <div className="frow" key={f.name}>{label}{control}{hint}</div>;
      })}
    </form>
  );
}
