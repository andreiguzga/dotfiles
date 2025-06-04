return {
  'neovim/nvim-lspconfig',
  ft = { 'php' },
  config = function()
    require('lspconfig').intelephense.setup {
      filetypes = { 'php' },
      settings = {
        intelephense = {
          diagnostics = {
            undefinedVariables = true,
            undefinedFunctions = true,
            typeMismatch = true,
          },
        },
      },
    }
  end,
}
