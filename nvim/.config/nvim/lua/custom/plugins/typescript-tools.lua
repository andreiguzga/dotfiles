return {
  'pmizio/typescript-tools.nvim',
  dependencies = { 'nvim-lua/plenary.nvim' },
  ft = {
    'typescript',
    'typescriptreact',
    'typescript.tsx',
    'javascript',
    'javascriptreact',
    'vue',
  },
  opts = {
    settings = {
      tsserver_format_options = {
        convertTabsToSpaces = true,
        tabSize = 2,
        indentSize = 2,
        newLineCharacter = '\n',
        insertSpaceAfterOpeningAndBeforeClosingNonemptyBraces = true,
      },
    },
  },
}
