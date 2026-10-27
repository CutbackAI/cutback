// Owned synthetic fixture only. No HTTP client, credentials, inference or token charge.
module.exports = class {
  id() { return "cutback-synthetic"; }
  async callApi(_prompt, context) {
    return {
      output: context.vars.input.includes("urgent") ? "urgent" : "normal",
      tokenUsage: { total: 0, prompt: 0, completion: 0 },
    };
  }
};
