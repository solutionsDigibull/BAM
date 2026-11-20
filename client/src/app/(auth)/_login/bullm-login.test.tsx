import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("../actions", () => ({ devSignIn: vi.fn(), keycloakSignIn: vi.fn() }));

import { BullmLogin } from "./bullm-login";

afterEach(cleanup);

describe("the FDE login in the BuLLM look (#719)", () => {
  test("test_719_keycloak_sign_in_asks_for_a_username_or_email_but_never_a_password", () => {
    const { container } = render(<BullmLogin portal="fde" enabled={false} keycloak />);
    expect(screen.getByRole("button", { name: "Sign in with Keycloak" })).toBeTruthy();
    expect(screen.getByLabelText("Username or email")).toBeTruthy();
    // the password is typed on Keycloak's own page: no field here can collect or accept one
    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(screen.queryByLabelText(/password/i)).toBeNull();
    expect(screen.getByText(/enter your password on the next secure step/i)).toBeTruthy();
  });

  test("test_719_the_form_sends_the_portal_and_the_callback_to_the_action", () => {
    const { container } = render(
      <BullmLogin portal="fde" enabled={false} keycloak callbackUrl="/projects/x" />,
    );
    expect((container.querySelector('input[name="portal"]') as HTMLInputElement).value).toBe("fde");
    expect((container.querySelector('input[name="callbackUrl"]') as HTMLInputElement).value).toBe(
      "/projects/x",
    );
  });

  test("test_719_a_username_without_an_at_sign_is_accepted_not_refused_as_a_bad_email", () => {
    const { container } = render(<BullmLogin portal="fde" enabled={false} keycloak />);
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
    render(<BullmLogin portal="fde" enabled={false} keycloak />);
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
    render(
      <BullmLogin
        portal="fde"
        enabled={false}
        keycloak
        error="Configuration"
        reference="1234-abcd"
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain(
      "Sign-in is unavailable, try again later.",
    );
    expect(screen.getByText("Reference: 1234-abcd")).toBeTruthy();
  });

  test("test_719_the_wrong_portal_and_no_role_errors_keep_their_wording", () => {
    const { rerender } = render(
      <BullmLogin portal="fde" enabled={false} keycloak error="WrongPortal" />,
    );
    expect(screen.getByText(/This account cannot use this portal/)).toBeTruthy();
    rerender(<BullmLogin portal="fde" enabled={false} keycloak error="NoRole" />);
    expect(screen.getByText(/has no SBAM role/)).toBeTruthy();
  });

  test("test_719_the_development_role_buttons_appear_only_when_keycloak_is_off", () => {
    const { rerender } = render(<BullmLogin portal="fde" enabled keycloak={false} />);
    expect(screen.getByRole("button", { name: "Sign in as FDE" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign in as Admin + FDE" })).toBeTruthy();
    // dev sign-in on AND Keycloak on: the shortcut would end in a 401 from the API, so it is not offered
    rerender(<BullmLogin portal="fde" enabled keycloak />);
    expect(screen.queryByRole("button", { name: "Sign in as FDE" })).toBeNull();
    expect(screen.getByRole("button", { name: "Sign in with Keycloak" })).toBeTruthy();
    // neither on: nothing to click, and the page says so
    rerender(<BullmLogin portal="fde" enabled={false} keycloak={false} />);
    expect(screen.queryByRole("button", { name: "Sign in as FDE" })).toBeNull();
  });

  test("test_719_with_no_sign_in_configured_the_page_says_so_instead_of_offering_one", () => {
    render(<BullmLogin portal="fde" enabled={false} keycloak={false} />);
    expect(screen.getByText("Keycloak sign-in is not configured yet.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /sign in/i })).toBeNull();
  });

  test("test_719_the_panel_describes_only_what_sbam_does_today", () => {
    render(<BullmLogin portal="fde" enabled={false} keycloak />);
    expect(document.body.textContent).not.toMatch(/staging|deploy/i);
  });
});

describe("the Admin login uses the same BuLLM login (#785)", () => {
  test("test_mui_6_t05_admin_posts_portal_admin_and_the_callback_to_keycloak_sign_in", () => {
    const { container } = render(
      <BullmLogin portal="admin" enabled={false} keycloak callbackUrl="/admin/masters" />,
    );
    expect(screen.getByRole("button", { name: "Sign in with Keycloak" })).toBeTruthy();
    expect((container.querySelector('input[name="portal"]') as HTMLInputElement).value).toBe(
      "admin",
    );
    expect((container.querySelector('input[name="callbackUrl"]') as HTMLInputElement).value).toBe(
      "/admin/masters",
    );
    expect(screen.getByRole("link", { name: "FDE? Use the FDE login" }).getAttribute("href")).toBe(
      "/login-fde",
    );
  });

  test("test_mui_6_t06_admin_development_choices_are_admin_and_super_admin", () => {
    const { container } = render(<BullmLogin portal="admin" enabled keycloak={false} />);
    expect(screen.getByRole("button", { name: "Sign in as Admin" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign in as Super-admin" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sign in as FDE" })).toBeNull();
    const roles = [...container.querySelectorAll<HTMLInputElement>('input[name="roles"]')];
    expect(roles.map((r) => r.value)).toEqual(["sbam-admin", "super-admin"]);
  });

  test("test_mui_6_t07_admin_errors_keep_their_wording_and_reference", () => {
    const { rerender } = render(
      <BullmLogin portal="admin" enabled={false} keycloak error="WrongPortal" />,
    );
    expect(screen.getByText(/This account cannot use this portal/)).toBeTruthy();
    rerender(<BullmLogin portal="admin" enabled={false} keycloak error="NoRole" />);
    expect(screen.getByText(/has no SBAM role/)).toBeTruthy();
    rerender(
      <BullmLogin portal="admin" enabled={false} keycloak error="Configuration" reference="r-1" />,
    );
    expect(screen.getByRole("alert").textContent).toContain(
      "Sign-in is unavailable, try again later.",
    );
    expect(screen.getByText("Reference: r-1")).toBeTruthy();
  });
});
