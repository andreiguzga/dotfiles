-- Neo-tree is a Neovim plugin to browse the file system
-- https://github.com/nvim-neo-tree/neo-tree.nvim

return {
  'nvim-neo-tree/neo-tree.nvim',
  version = '*',
  dependencies = {
    'nvim-lua/plenary.nvim',
    'nvim-tree/nvim-web-devicons',
    'MunifTanjim/nui.nvim',
  },
  cmd = 'Neotree',
  keys = {
    { '\\', ':Neotree reveal<CR>', desc = 'NeoTree reveal', silent = true },
  },
  opts = {
    filesystem = {
      bind_to_cwd = false, -- Prevent auto root change
      cwd_target = 'none', -- Don't update Neovim's cwd
      follow_current_file = {
        enabled = false, -- Optional: don't follow file focus
      },
      window = {
        mappings = {
          ['\\'] = 'close_window',
          ['.'] = function(state)
            local node = state.tree:get_node()
            local path = node:get_id()
            require('neo-tree.sources.filesystem').navigate(state, path)
            vim.cmd('cd ' .. path) -- updates Neovim's cwd
            print('Changed cwd to: ' .. path)
          end,
        },
      },
    },
  },
}
