"use client";

// The FDE's landing page after sign-in (#719): the projects the Admin assigned, in the BuLLM look. A click opens
// the project in the workbench, which resumes where the work is. An FDE never creates a project: only an Admin does.
import { useList } from "@refinedev/core";
import Link from "next/link";

import wordmarkDark from "@/app/(auth)/_login/assets/bullm-wordmark-dark.png";
import wordmarkLight from "@/app/(auth)/_login/assets/bullm-wordmark-light.png";
import type { Project } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import "./projects-landing.css";
import { EMPTY_PROJECTS, actionLabel, stateOf, workbenchHref } from "./project-state";

export function ProjectsLanding({ user, signOut }: { user: string; signOut: () => Promise<void> }) {
  const { result, query } = useList<Project, ApiError>({
    resource: "projects",
    pagination: { mode: "off" },
  });
  const projects = result.data ?? [];
  const status = query.error?.statusCode;

  return (
    <main className="pl-page">
      <header className="pl-top">
        <picture>
          <source media="(prefers-color-scheme: light)" srcSet={wordmarkLight.src} />
          <img
            src={wordmarkDark.src}
            alt="BuLLM. Enterprise Workflows. Intelligently Done."
            className="pl-logo"
            width={170}
          />
        </picture>
        <form action={signOut} className="pl-user">
          <span className="pl-email">{user}</span>
          <button type="submit" className="pl-link">
            Sign out
          </button>
        </form>
      </header>

      <section className="pl-body" aria-labelledby="pl-title">
        <h1 id="pl-title" className="pl-title">
          Your projects
        </h1>
        <p className="pl-lede">Pick a project to continue where you left off.</p>

        {query.isLoading ? (
          <p className="pl-note" role="status">
            Loading your projects…
          </p>
        ) : query.error ? (
          <div className="pl-alert" role="alert">
            <p>
              {status === 403
                ? "You do not have access to these projects."
                : (query.error.message ?? "Your projects could not be loaded.")}
            </p>
            {status !== 403 && (
              <button type="button" className="pl-btn" onClick={() => void query.refetch()}>
                Try again
              </button>
            )}
          </div>
        ) : projects.length === 0 ? (
          <p className="pl-empty">{EMPTY_PROJECTS}</p>
        ) : (
          <ul className="pl-grid">
            {projects.map((p) => {
              const s = stateOf(p.pipeline_state);
              return (
                <li key={p.id} className="pl-card">
                  <div className="pl-card-head">
                    <h2 className="pl-name">{p.name}</h2>
                    <span className={`pl-chip tone-${s.tone}`}>{s.label}</span>
                  </div>
                  {p.client_name ? <p className="pl-client">{p.client_name}</p> : null}
                  {p.description ? <p className="pl-desc">{p.description}</p> : null}
                  <Link
                    href={workbenchHref(p.id)}
                    className="pl-open"
                    aria-label={`Open ${p.name}`}
                  >
                    {actionLabel(p.pipeline_state)}
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </main>
  );
}
