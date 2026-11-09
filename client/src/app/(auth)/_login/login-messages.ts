// Showcase messages on the login panel. Add or remove one line; the carousel adapts.
// Each line describes something SBAM does today: no staging or deploy claims.
export interface LoginMessage {
  headline: string;
  flow: string;
}

export const LOGIN_MESSAGES: LoginMessage[] = [
  {
    headline: "From discovery document to certified skill, in one pipeline.",
    flow: "Ingest > Retrieve > Build > Validate",
  },
  {
    headline: "Every SOW traces back to its source documents.",
    flow: "Sources > Evidence > Review > Approve",
  },
  {
    headline: "Validated code, tested before it ships.",
    flow: "Generate > Validate > Test > Certify",
  },
];
