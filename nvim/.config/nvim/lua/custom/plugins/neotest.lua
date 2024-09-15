return {
  {
    'nvim-neotest/neotest',
    dependencies = {
      'nvim-neotest/nvim-nio',
      'nvim-lua/plenary.nvim',
      'antoinemadec/FixCursorHold.nvim',
      'nvim-treesitter/nvim-treesitter',
      'nvim-neotest/neotest-jest',
    },
    config = function()
      require('neotest').setup {
        adapters = {
          require 'neotest-jest' {
            filetypes = { 'javascript', 'typescript', 'typescriptreact' },
            jestCommand = 'yarn test',
            testMatch = { '**/?(*.)+(spec|test).[jt]s?(x)' },
            env = { CI = true },
            cwd = function()
              return vim.fn.getcwd()
            end,
          },
        },
        discovery = {
          enabled = true,
        },
      }

      -- Keybindings for Neotest with descriptions for which-key
      vim.api.nvim_set_keymap('n', '<leader>tt', "<cmd>lua require('neotest').run.run()<CR>", { noremap = true, silent = true, desc = 'Run [T]est Nearest' })
      vim.api.nvim_set_keymap(
        'n',
        '<leader>tw',
        "<cmd>lua require('neotest').run.run({ jestCommand = 'jest --watch ' })<CR>",
        { noremap = true, silent = true, desc = 'Run Test with [W]atch' }
      )
      vim.api.nvim_set_keymap(
        'n',
        '<leader>tf',
        "<cmd>lua require('neotest').run.run(vim.fn.expand('%'))<CR>",
        { noremap = true, silent = true, desc = 'Run Test [F]ile' }
      )
      vim.api.nvim_set_keymap(
        'n',
        '<leader>ta',
        "<cmd>lua require('neotest').run.run({ suite = true })<CR>",
        { noremap = true, silent = true, desc = 'Run [A]ll Tests' }
      )
      vim.api.nvim_set_keymap('n', '<leader>tx', "<cmd>lua require('neotest').run.stop()<CR>", { noremap = true, silent = true, desc = 'Sto[p] Running Tests' })
      vim.api.nvim_set_keymap(
        'n',
        '<leader>ts',
        "<cmd>lua require('neotest').summary.toggle()<CR>",
        { noremap = true, silent = true, desc = 'Toggle [S]ummary Window' }
      )
      vim.api.nvim_set_keymap(
        'n',
        '<leader>to',
        "<cmd>lua require('neotest').output.open({ enter = true })<CR>",
        { noremap = true, silent = true, desc = 'Show Test [O]utput' }
      )
      vim.api.nvim_set_keymap(
        'n',
        '<leader>tO',
        "<cmd>lua require('neotest').output_panel.toggle()<CR>",
        { noremap = true, silent = true, desc = 'Toggle [O]utput Panel' }
      )
      vim.api.nvim_set_keymap(
        'n',
        '<leader>tn',
        "<cmd>lua require('neotest').jump.next({ status = 'failed' })<CR>",
        { noremap = true, silent = true, desc = 'Jump to [N]ext Failed Test' }
      )
      vim.api.nvim_set_keymap(
        'n',
        '<leader>tp',
        "<cmd>lua require('neotest').jump.prev({ status = 'failed' })<CR>",
        { noremap = true, silent = true, desc = 'Jump to [P]revious Failed Test' }
      )
      vim.api.nvim_set_keymap(
        'n',
        '<leader>tA',
        "<cmd>lua require('neotest').run.attach()<CR>",
        { noremap = true, silent = true, desc = '[A]ttach to Nearest Test' }
      )
    end,
  },
}
