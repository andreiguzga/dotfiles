return {
  {
    'mechatroner/rainbow_csv',
    ft = { 'csv', 'tsv', 'csv_semicolon' }, -- only load on CSV-related filetypes
    config = function()
      -- Optional: customize plugin settings if needed
      vim.g.rainbow_csv_delim = ','
    end,
  },
}
