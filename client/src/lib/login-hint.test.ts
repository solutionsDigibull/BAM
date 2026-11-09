import { describe, expect, test } from "vitest";

import { loginHint } from "./login-hint";

describe("what the login card may pass to Keycloak as a hint (#719)", () => {
  test("test_719_a_username_and_an_email_are_both_passed_on", () => {
    expect(loginHint("fde-a1")).toBe("fde-a1");
    expect(loginHint("fde-a1@sbam.test")).toBe("fde-a1@sbam.test");
    expect(loginHint("  harish.t@digibull.ai  ")).toBe("harish.t@digibull.ai");
  });

  test("test_719_nothing_typed_means_no_hint", () => {
    expect(loginHint("")).toBeUndefined();
    expect(loginHint("   ")).toBeUndefined();
    expect(loginHint(null)).toBeUndefined();
    expect(loginHint(undefined)).toBeUndefined();
    expect(loginHint(42)).toBeUndefined();
  });

  test("test_719_text_that_could_break_the_redirect_is_dropped", () => {
    expect(loginHint("two words")).toBeUndefined();
    expect(loginHint("line\nbreak")).toBeUndefined();
    expect(loginHint("tab\there")).toBeUndefined();
    expect(loginHint("nul\u0000byte")).toBeUndefined();
    expect(loginHint("a".repeat(255))).toBeUndefined();
    expect(loginHint("a".repeat(254))).toBe("a".repeat(254));
  });
});
