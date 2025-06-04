return {
  'neovim/nvim-lspconfig',
  -- no ft = …  (let the main spec decide when to load)
  opts = function(_, opts)
    opts.servers = opts.servers or {}
    opts.servers.intelephense = vim.tbl_deep_extend('force', opts.servers.intelephense or {}, {
      settings = {
        intelephense = {
          diagnostics = {
            undefinedVariables = true,
            undefinedFunctions = true,
            typeMismatch = true,
          },
        },
      },
    })
  end,
}
