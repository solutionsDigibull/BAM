import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("../actions", () => ({ devSignIn: vi.fn(), keycloakSignIn: vi.fn() }));

import { FdeLogin } from "./fde-login";

afterEach(cleanup);

describe("the FDE login in the BuLLM look (#719)", () => {
  test("test_719_keycloak_sign_in_asks_for_a_username_or_email_but_never_a_password", () => {
    const { container } = render(<FdeLogin enabled={false} keycloak />);
    expect(screen.getByRole("button", { name: "Sign in with Keycloak" })).toBeTruthy();
    expect(screen.getByLabelText("Username or email")).toBeTruthy();
    // the password is typed on Keycloak's own page: no field here can collect or accept one
    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(screen.queryByLabelText(/password/i)).toBeNull();
    expect(screen.getByText(/enter your password on the next secure step/i)).toBeTruthy();
  });

  test("test_719_the_form_sends_the_portal_and_the_callback_to_the_action", () => {
    const { container } = render(<FdeLogin enabled={false} keycloak callbackUrl="/projects/x" />);
    expect((container.querySelector('input[name="portal"]') as HTMLInputElement).value).toBe("fde");
    expect((container.querySelector('input[name="callbackUrl"]') as HTMLInputElement).value).toBe(
      "/projects/x",
    );
  });

  test("test_719_a_username_without_an_at_sign_is_accepted_not_refused_as_a_bad_email", () => {
    const { container } = render(<FdeLogin enabled={false} keycloak />);
    const input = screen.getByLabelText("Username or email") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "fde-a1" } });
    fireEvent.submit(
      screen.getByRole("button", { name: "Sign in with Keycloak" }).closest("form")!,
    );
    expect(input.value).toBe("fde-a1");
    expect(input.name).toBe("username"); // the name the sign-in action reads
    expect(container.querySelector('[aria-invalid="true"]')).toBeNull();
    expect(screen.queryByText(/enter a valid/i)).toBeNull();
  });

  test("test_719_an_email_and_an_empty_field_are_accepted_too_because_keycloak_asks_for_it", () => {
    render(<FdeLogin enabled={false} keycloak />);
    const input = screen.getByLabelText("Username or email") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "fde-a1@sbam.test" } });
    expect(input.value).toBe("fde-a1@sbam.test");
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.submit(
      screen.getByRole("button", { name: "Sign in with Keycloak" }).closest("form")!,
    );
    expect(screen.queryByText(/enter a valid/i)).toBeNull();
  });

  test("test_719_an_error_shows_its_text_and_the_reference_as_separate_lines", () => {
    render(<FdeLogin enabled={false} keycloak error="Configuration" reference="1234-abcd" />);
    expect(screen.getByRole("alert").textContent).toContain(
      "Sign-in is unavailable, try again later.",
    );
    expect(screen.getByText("Reference: 1234-abcd")).toBeTruthy();
  });

  test("test_719_the_wrong_portal_and_no_role_errors_keep_their_wording", () => {
    const { rerender } = render(<FdeLogin enabled={false} keycloak error="WrongPortal" />);
    expect(screen.getByText(/This account cannot use this portal/)).toBeTruthy();
    rerender(<FdeLogin enabled={false} keycloak error="NoRole" />);
    expect(screen.getByText(/has no SBAM role/)).toBeTruthy();
  });

  test("test_719_the_development_role_buttons_appear_only_when_keycloak_is_off", () => {
    const { rerender } = render(<FdeLogin enabled keycloak={false} />);
    expect(screen.getByRole("button", { name: "Sign in as FDE" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign in as Admin + FDE" })).toBeTruthy();
    // dev sign-in on AND Keycloak on: the shortcut would end in a 401 from the API, so it is not offered
    rerender(<FdeLogin enabled keycloak />);
    expect(screen.queryByRole("button", { name: "Sign in as FDE" })).toBeNull();
    expect(screen.getByRole("button", { name: "Sign in with Keycloak" })).toBeTruthy();
    // neither on: nothing to click, and the page says so
    rerender(<FdeLogin enabled={false} keycloak={false} />);
    expect(screen.queryByRole("button", { name: "Sign in as FDE" })).toBeNull();
  });

  test("test_719_with_no_sign_in_configured_the_page_says_so_instead_of_offering_one", () => {
    render(<FdeLogin enabled={false} keycloak={false} />);
    expect(screen.getByText("Keycloak sign-in is not configured yet.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /sign in/i })).toBeNull();
  });

  test("test_719_the_panel_describes_only_what_sbam_does_today", () => {
    render(<FdeLogin enabled={false} keycloak />);
    expect(document.body.textContent).not.toMatch(/staging|deploy/i);
  });
});
