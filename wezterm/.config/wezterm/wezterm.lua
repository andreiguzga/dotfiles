-- Pull in the wezterm API
local wezterm = require("wezterm")

-- This will hold the configuration.
local config = wezterm.config_builder()

-- This is where you actually apply your config choices

-- For example, changing the color scheme:
config.color_scheme = "tokyonight_night"

config.font = wezterm.font("DejaVuSansM Nerd Font Mono")
config.font_size = 12.0

-- Hide the tab bar when only one tab is open
config.hide_tab_bar_if_only_one_tab = true

-- Disable setting the window title
config.window_decorations = "RESIZE" -- Removes title bar but keeps resizing ability

-- Set the background transparency
config.window_background_opacity = 0.9
config.text_background_opacity = 1.0
config.macos_window_background_blur = 10

config.front_end = "OpenGL"
config.scrollback_lines = 10000
config.enable_vi_mode = true

-- and finally, return the configuration to wezterm
return config
