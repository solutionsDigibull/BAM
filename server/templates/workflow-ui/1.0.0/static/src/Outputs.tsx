// Result cards of one sub-skill: one card per output field, in the view the generator chose from the
// output schema (table, editable text or key/value).
import { IconFileSpreadsheet } from "@tabler/icons-react";
import type { OutField } from "./types";
import { DataGrid } from "./Grid";
import { TextEditor } from "./Editor";

export type Result = Record<string, unknown>;

const show = (v: unknown): string => (v === null || v === undefined ? "-" : typeof v === "object" ? JSON.stringify(v) : String(v));

export function Outputs({ fields, result, dark }: { fields: OutField[]; result: Result; dark: boolean }) {
  const known = new Set(fields.map(f => f.name));
  const extra = Object.keys(result).filter(k => !known.has(k));
  return (
    <>
      {fields.filter(f => f.name in result).map(f => {
        const value = result[f.name];
        return (
          <div className="ocard" key={f.name}>
            <h3><span className="oi"><IconFileSpreadsheet size={15} /></span>{f.label}</h3>
            {f.view === "grid" && Array.isArray(value) && value.length > 0 ? (
              <DataGrid dark={dark} rows={value.map(v => (typeof v === "object" && v !== null ? (v as Record<string, unknown>) : { value: v }))} />
            ) : f.view === "editor" && typeof value === "string" ? (
              <TextEditor content={value} />
            ) : (
              <div className="kv"><span className="k">{f.description ?? f.label}</span><span className="v">{show(value)}</span></div>
            )}
          </div>
        );
      })}
      {extra.length > 0 && (
        <div className="ocard">
          <h3><span className="oi"><IconFileSpreadsheet size={15} /></span>Other results</h3>
          {extra.map(k => <div className="kv" key={k}><span className="k">{k.replace(/_/g, " ")}</span><span className="v">{show(result[k])}</span></div>)}
        </div>
      )}
    </>
  );
}
