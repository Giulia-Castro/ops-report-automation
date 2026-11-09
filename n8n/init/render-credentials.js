// Builds the n8n credentials file from environment variables, so no secret is ever
// committed in a workflow or credential export. n8n encrypts them on import with
// N8N_ENCRYPTION_KEY. Output goes to stdout and is deleted right after the import.
"use strict";

function required(name) {
  const value = process.env[name];
  if (!value) {
    console.error(`Missing required environment variable: ${name}`);
    process.exit(1);
  }
  return value;
}

const credentials = [
  {
    id: "OpsEngineApiKey1",
    name: "Report Engine API key",
    type: "httpHeaderAuth",
    data: { name: "X-API-Key", value: required("REPORT_ENGINE_API_KEY") },
  },
  {
    id: "OpsAnthropicKey1",
    name: "Anthropic API key",
    type: "httpHeaderAuth",
    // Without a key the Claude call fails and the report uses the rule-based summary.
    data: { name: "x-api-key", value: process.env.ANTHROPIC_API_KEY || "not-configured" },
  },
  {
    id: "OpsSmtpServer001",
    name: "SMTP server",
    type: "smtp",
    data: {
      host: required("SMTP_HOST"),
      port: Number(process.env.SMTP_PORT || 587),
      user: process.env.SMTP_USER || "",
      password: process.env.SMTP_PASSWORD || "",
      secure: process.env.SMTP_SECURE === "true",
      disableStartTls: process.env.SMTP_DISABLE_STARTTLS === "true",
    },
  },
];

process.stdout.write(JSON.stringify(credentials));
