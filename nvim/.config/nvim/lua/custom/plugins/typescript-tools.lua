return {
  'pmizio/typescript-tools.nvim',
  dependencies = { 'nvim-lua/plenary.nvim' },
  ft = { 'typescript', 'typescriptreact', 'javascript', 'javascriptreact' },
  opts = {
    separate_diagnostic_server = true,
    publish_diagnostic_on = 'insert_leave',
    expose_as_code_action = 'all',
    tsserver_plugins = {},
    on_attach = function(client, bufnr)
      -- You can add custom LSP mappings here if needed
    end,
  },
}
