import type { Plugin } from "@opencode-ai/plugin"

const COMMENT_TOOL = "asana_add_comment"

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function fixSchema(value: unknown) {
  if (!isObject(value)) return

  return {
    ...value,
    ...(Array.isArray(value.required)
      ? { required: value.required.filter((name) => name !== "text" && name !== "html_text") }
      : {}),
    anyOf: [{ required: ["text"] }, { required: ["html_text"] }],
  }
}

function fixArguments(value: unknown) {
  if (!isObject(value)) return

  if (typeof value.text === "string" && value.text.trim()) {
    delete value.html_text
  } else if (typeof value.html_text === "string" && value.html_text.trim()) {
    delete value.text
  }
}

// Keep the runtime dependency-free while V1 and V2 use different SDK packages.
// This is the subset of the V2 Promise plugin context used below.
interface V2Context {
  tool: {
    transform(callback: (editor: {
      list(): readonly { id: string }[]
      update(id: string, callback: (tool: { input: unknown }) => void): void
    }) => void): Promise<unknown>
    hook(name: "execute.before", callback: (event: { tool: string; input: unknown }) => void): Promise<unknown>
  }
}

const server = (async () => {
  return {
    async "tool.definition"(input, output) {
      if (!input.toolID.includes(COMMENT_TOOL)) return

      const schema = fixSchema(output.parameters)
      if (schema) output.parameters = schema
    },

    async "tool.execute.before"(input, output) {
      if (!input.tool.includes(COMMENT_TOOL)) return

      fixArguments(output.args)
    },
  }
}) satisfies Plugin

// V1 >= 1.18.29 calls server(); V2 calls setup().
export default {
  id: "local.asana-comment-schema-fix",
  server,
  async setup(ctx: V2Context) {
    await ctx.tool.transform((editor) => {
      for (const tool of editor.list()) {
        if (!tool.id.includes(COMMENT_TOOL)) continue
        editor.update(tool.id, (definition) => {
          const schema = fixSchema(definition.input)
          if (schema) definition.input = schema
        })
      }
    })

    await ctx.tool.hook("execute.before", (event) => {
      if (event.tool.includes(COMMENT_TOOL)) fixArguments(event.input)
    })
  },
}
