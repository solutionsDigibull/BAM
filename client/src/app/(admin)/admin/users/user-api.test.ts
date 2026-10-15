import { describe, expect, test } from "vitest";

import { isEmail } from "./user-api";

describe("isEmail mirrors the API's RFC 5322 check (E2.8)", () => {
  test.each([
    "fde3@acme.com",
    " New@Acme.com ",
    "admin-a@sbam.test",
    "pat+invite@acme.example",
    "first.last@sub.acme.co.uk",
  ])("accepts %s", (email) => expect(isEmail(email)).toBe(true));

  test.each([
    "abc",
    "a@b", // no dot in the domain
    '"x"@', // quoted local part, no domain
    '"quoted"@acme.com',
    "x@y..z",
    "a b@acme.com",
    "@acme.com",
    "x@-acme.com",
    ".x@acme.com",
    `${"a".repeat(65)}@acme.com`,
    `a@${"b".repeat(250)}.com`,
  ])("rejects %s", (email) => expect(isEmail(email)).toBe(false));
});
