return {
  {
    'ahmedkhalf/project.nvim',
    config = function()
      require('project_nvim').setup {}
      require('telescope').load_extension 'projects'

      -- Keybinding to open the Telescope projects extension
      vim.api.nvim_set_keymap(
        'n',
        '<leader>p', -- Adjust this keybinding as needed
        "<cmd>lua require('telescope').extensions.projects.projects{}<CR>",
        { noremap = true, silent = true, desc = 'Open [P]rojects with Telescope' }
      )
    end,
  },
}
