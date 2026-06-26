import type { Plugin } from "@opencode-ai/plugin"

const COMMENT_TOOL = "asana_add_comment"

export default (async () => {
  return {
    async "tool.definition"(input, output) {
      if (!input.toolID.includes(COMMENT_TOOL)) return

      const params = output.parameters
      if (!params || typeof params !== "object") return

      if (Array.isArray(params.required)) {
        params.required = params.required.filter(
          (name: string) => name !== "text" && name !== "html_text",
        )
      }

      params.anyOf = [
        { required: ["text"] },
        { required: ["html_text"] },
      ]
    },

    async "tool.execute.before"(input, output) {
      if (!input.tool.includes(COMMENT_TOOL)) return

      const args = output.args
      if (!args || typeof args !== "object") return

      if (typeof args.text === "string" && args.text.trim()) {
        delete args.html_text
        return
      }

      if (typeof args.html_text === "string" && args.html_text.trim()) {
        delete args.text
      }
    },
  }
}) satisfies Plugin
