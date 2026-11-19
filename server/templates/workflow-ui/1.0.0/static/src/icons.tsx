import {
  IconBolt, IconChartBar, IconClipboardCheck, IconDatabase, IconFiles, IconGauge, IconInbox,
  IconLink, IconMail, IconScan, IconTable, IconTruck,
} from "@tabler/icons-react";
import bullHead from "./assets/bull-head.png";

// Same glyphs as BAM_UI: the BuLLM bull marks an automated step, the person outline a human one.
export function HumanIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  );
}

export function BullIcon({ size = 20 }: { size?: number }) {
  return <img className="bull" src={bullHead} alt="Automated" width={size} height={size} />;
}

export function ModeIcon({ mode, size }: { mode: "human" | "auto"; size?: number }) {
  return mode === "human" ? <HumanIcon size={size} /> : <BullIcon size={size} />;
}

// The vocabulary the generator draws a skill icon from.
export const ICONS: Record<string, typeof IconBolt> = {
  inbox: IconInbox, scan: IconScan, truck: IconTruck, clipboard: IconClipboardCheck,
  database: IconDatabase, table: IconTable, chart: IconChartBar, link: IconLink,
  mail: IconMail, bolt: IconBolt, gauge: IconGauge, files: IconFiles,
};
