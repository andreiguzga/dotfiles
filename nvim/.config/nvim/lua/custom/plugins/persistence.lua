return {
  {
    'folke/persistence.nvim',
    event = 'VimLeavePre', -- auto-save when exiting Neovim
    cmd = { 'SessionSave', 'SessionRestore', 'SessionStop' },
    opts = {},
  },
}
