import assert from "node:assert/strict"
import { test } from "node:test"
import plugin from "../.config/opencode/plugins/asana-comment-schema-fix.ts"

const comment = "asana_asana_add_comment"
const original = {
  type: "object",
  properties: { task: { type: "string" }, text: { type: "string" }, html_text: { type: "string" } },
  required: ["task", "text", "html_text"],
  additionalProperties: false,
}

async function adapters() {
  const v1 = await plugin.server()
  let transform
  let before
  await plugin.setup({
    tool: {
      async transform(callback) { transform = callback },
      async hook(name, callback) {
        assert.equal(name, "execute.before")
        before = callback
      },
    },
  })
  return [
    {
      name: "V1",
      async schema(id, input) {
        const output = { parameters: input }
        await v1["tool.definition"]({ toolID: id }, output)
        return output.parameters
      },
      async args(id, input) {
        await v1["tool.execute.before"]({ tool: id }, { args: input })
      },
    },
    {
      name: "V2",
      async schema(id, input) {
        const tool = { id, input }
        transform({
          list: () => [tool],
          update(target, callback) {
            assert.equal(target, id)
            callback(tool)
          },
        })
        return tool.input
      },
      async args(id, input) { before({ tool: id, input }) },
    },
  ]
}

for (const api of await adapters()) {
  test(`${api.name}: corrects comment alternatives without mutating the source catalog`, async () => {
    const input = structuredClone(original)
    Object.freeze(input.required)
    Object.freeze(input)
    const result = await api.schema(comment, input)
    assert.deepEqual(result.required, ["task"])
    assert.deepEqual(result.anyOf, [{ required: ["text"] }, { required: ["html_text"] }])
    assert.deepEqual(result.properties, original.properties)
    assert.equal(result.additionalProperties, false)
    assert.deepEqual(input, original)
    assert.deepEqual(await api.schema(comment, result), result)
  })

  test(`${api.name}: leaves unrelated schemas and arguments untouched`, async () => {
    const input = structuredClone(original)
    assert.equal(await api.schema("asana_get_task", input), input)
    const args = { text: "plain", html_text: "<body>HTML</body>" }
    await api.args("asana_get_task", args)
    assert.deepEqual(args, { text: "plain", html_text: "<body>HTML</body>" })
  })

  test(`${api.name}: prefers nonblank plain text, otherwise nonblank HTML`, async () => {
    for (const [input, expected] of [
      [{ task: "123", text: "plain", html_text: "<body>HTML</body>" }, { task: "123", text: "plain" }],
      [{ text: "  ", html_text: "<body>HTML</body>" }, { html_text: "<body>HTML</body>" }],
      [{ text: "plain" }, { text: "plain" }],
      [{ html_text: "<body>HTML</body>" }, { html_text: "<body>HTML</body>" }],
      [{ text: "", html_text: " " }, { text: "", html_text: " " }],
    ]) {
      await api.args(comment, input)
      assert.deepEqual(input, expected)
    }
  })

  test(`${api.name}: tolerates missing or non-object input`, async () => {
    for (const input of [undefined, null, "invalid", []]) {
      assert.equal(await api.schema(comment, input), input)
      await api.args(comment, input)
    }
    const result = await api.schema(comment, { type: "object" })
    assert.deepEqual(result.anyOf, [{ required: ["text"] }, { required: ["html_text"] }])
  })
}
