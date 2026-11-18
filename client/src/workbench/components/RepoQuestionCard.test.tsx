import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { PendingRepoQuestion, RepoQuestions } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { RepoQuestionCard } from "./RepoQuestionCard";

const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const A = "https://github.com/acme/stripe-webhooks";
const B = "https://github.com/acme/payments-kit";
const QUESTION = (over: Partial<PendingRepoQuestion["question"]> = {}): PendingRepoQuestion => ({
  sub_skill_name: "Verify payment",
  build_no: 1,
  question: {
    sub_skill_id: SUB,
    kind: "CLOSE_CANDIDATES",
    asked_at: "2026-11-18T09:00:00Z",
    candidates: [
      { url: A, source: "ADMIN_AGENTS", score: 0.86, reasons: ["notes name the Stripe webhooks"] },
      { url: B, source: "ADMIN_TOOLING", score: 0.82, reasons: ["same payment domain"] },
    ],
    ...over,
  },
});
const DATA = (questions: PendingRepoQuestion[]): RepoQuestions => ({ workflow_id: "w1", questions, decisions: [] });

const install = (over: Partial<BamApi>) =>
  setBamApi({ getRepoQuestions: async () => DATA([QUESTION()]), answerRepoQuestion: async () => ({ sub_skill_id: SUB, build_no: 1 }), ...over } as unknown as BamApi);
const renderCard = (id: string | undefined = "w1", quiet?: boolean) =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        <RepoQuestionCard workflowId={id} quiet={quiet} />
      </Refine>
    </div>,
  );

afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("the build asks the FDE which repository to start from (#756)", () => {
  test("test_756_the_card_shows_the_short_list_with_scores_and_reasons", async () => {
    install({});
    renderCard();
    const card = await screen.findByRole("region", { name: "Repository for Verify payment" });
    expect(card.textContent).toContain("Several repositories fit about equally well");
    expect(card.textContent).toContain("The build is paused until you answer");
    const list = within(card).getByRole("list", { name: "Short list" });
    const rows = Array.from(list.querySelectorAll("li[data-repo]"));
    expect(rows).toHaveLength(2);
    expect(rows[0]!.textContent).toContain("acme/stripe-webhooks");
    expect(rows[0]!.textContent).toContain("86% match");
    expect(rows[0]!.textContent).toContain("Admin: Agents master");
    expect(within(card).getByRole("list", { name: "Why acme/stripe-webhooks fits" }).textContent).toContain("notes name the Stripe webhooks");
  });

  test("test_756_choosing_a_repository_sends_the_answer_with_one_idempotency_key", async () => {
    const answer = vi.fn(async () => ({ sub_skill_id: SUB, build_no: 1 }));
    install({ answerRepoQuestion: answer });
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Use acme/payments-kit" }));
    await waitFor(() => expect(answer).toHaveBeenCalledTimes(1));
    const [workflow, sub, body, key] = answer.mock.calls[0] as unknown as [string, string, unknown, string];
    expect([workflow, sub, body]).toEqual(["w1", SUB, { action: "CHOOSE", url: B }]);
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    expect(await screen.findByText(/Answer sent/)).toBeTruthy();
  });

  test("test_756_none_of_these_sends_no_repo_so_the_code_is_generated", async () => {
    const answer = vi.fn(async () => ({ sub_skill_id: SUB, build_no: 1 }));
    install({ answerRepoQuestion: answer });
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "None of these: generate the code" }));
    await waitFor(() => expect(answer).toHaveBeenCalled());
    expect((answer.mock.calls[0] as unknown as [string, string, unknown])[2]).toEqual({ action: "NO_REPO" });
  });

  test("test_756_with_no_candidate_the_card_asks_whether_the_fde_has_a_repository", async () => {
    const answer = vi.fn(async () => ({ sub_skill_id: SUB, build_no: 1 }));
    install({ getRepoQuestions: async () => DATA([QUESTION({ kind: "NO_CANDIDATE", candidates: [] })]), answerRepoQuestion: answer });
    renderCard();
    const card = await screen.findByRole("region", { name: "Repository for Verify payment" });
    expect(card.textContent).toContain("The catalogue has no repository for this Sub-Skill");
    expect(within(card).queryByRole("list", { name: "Short list" })).toBeNull();
    const submit = within(card).getByRole("button", { name: "Use this repository" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true); // nothing typed yet
    const input = within(card).getByLabelText("Your repository (https address)");
    fireEvent.change(input, { target: { value: "http://github.com/me/mine" } });
    expect(submit.disabled).toBe(true); // not https
    fireEvent.change(input, { target: { value: "https://github.com/me/mine" } });
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);
    await waitFor(() => expect(answer).toHaveBeenCalled());
    expect((answer.mock.calls[0] as unknown as [string, string, unknown])[2]).toEqual({ action: "CHOOSE", url: "https://github.com/me/mine" });
  });

  test("test_756_no_candidate_and_no_repository_generates_the_code", async () => {
    const answer = vi.fn(async () => ({ sub_skill_id: SUB, build_no: 1 }));
    install({ getRepoQuestions: async () => DATA([QUESTION({ kind: "NO_CANDIDATE", candidates: [] })]), answerRepoQuestion: answer });
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "I have no repository: generate the code" }));
    await waitFor(() => expect(answer).toHaveBeenCalled());
    expect((answer.mock.calls[0] as unknown as [string, string, unknown])[2]).toEqual({ action: "NO_REPO" });
  });

  test("test_756_a_refused_answer_shows_the_servers_words_and_can_be_tried_again", async () => {
    const answer = vi.fn()
      .mockRejectedValueOnce(Object.assign(new Error("Invalid answer"), { statusCode: 422, problem: { errors: [{ message: "Choose one of the repositories on the short list, or say none fits." }] } }))
      .mockResolvedValue({ sub_skill_id: SUB, build_no: 1 });
    install({ answerRepoQuestion: answer });
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Use acme/stripe-webhooks" }));
    expect(await screen.findByText("Choose one of the repositories on the short list, or say none fits.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Use acme/stripe-webhooks" }));
    await waitFor(() => expect(screen.getByText(/Answer sent/)).toBeTruthy());
    expect(answer).toHaveBeenCalledTimes(2);
    expect(answer.mock.calls[0]![3]).toBe(answer.mock.calls[1]![3]); // a retry is the same command
  });

  test("test_756_loading_then_an_error_with_the_servers_detail_and_a_retry_that_reloads", async () => {
    const get = vi.fn()
      .mockRejectedValueOnce(Object.assign(new Error("Boom"), { statusCode: 500, problem: { detail: "The audit trail is unavailable" } }))
      .mockResolvedValue(DATA([QUESTION()]));
    install({ getRepoQuestions: get });
    renderCard();
    expect(screen.getByLabelText("Loading the repository question")).toBeTruthy();
    expect(await screen.findByText("Could not load the repository question")).toBeTruthy();
    expect(screen.getByText("The audit trail is unavailable")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByRole("region", { name: "Repository for Verify payment" })).toBeTruthy());
    expect(get).toHaveBeenCalledTimes(2);
  });

  test("test_756_nothing_waiting_shows_the_empty_state_and_the_quiet_card_shows_nothing", async () => {
    install({ getRepoQuestions: async () => DATA([]) });
    const full = renderCard();
    expect(await screen.findByText("Nothing to decide")).toBeTruthy();
    full.unmount();
    const quiet = renderCard("w1", true);
    await waitFor(() => expect(quiet.container.querySelector(".rq, .state, .skeleton")).toBeNull());
  });

  test("test_756_an_unassigned_fde_or_another_tenant_sees_the_forbidden_state", async () => {
    install({ getRepoQuestions: () => Promise.reject(Object.assign(new Error("Not found"), { statusCode: 404 })) });
    renderCard();
    expect(await screen.findByText("You can't answer for this workflow")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  test("test_756_before_the_build_has_started_there_is_no_workflow_to_ask_about", () => {
    const get = vi.fn();
    install({ getRepoQuestions: get });
    renderCard("");
    expect(screen.getByText("No question yet")).toBeTruthy();
    expect(get).not.toHaveBeenCalled();
  });
});
