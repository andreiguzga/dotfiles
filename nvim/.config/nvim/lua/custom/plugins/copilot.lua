return {
  {
    'github/copilot.vim',
    config = function()
      vim.g.copilot_filetypes = {
        sql = false,
        mysql = false,
        plsql = false,
      }
    end,
  },
}
