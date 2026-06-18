import { existsSync } from "node:fs"
import { spawn, spawnSync } from "node:child_process"

export const TmuxAttentionPlugin = async ({ $ }) => {
  const inTmux = !!process.env.TMUX
  const targetPane = process.env.TMUX_PANE
  let lastSoundAt = 0

  const commandExists = (command) => {
    return spawnSync("command", ["-v", command], { shell: true, stdio: "ignore" }).status === 0
  }

  const spawnSound = (command, args) => {
    if (!commandExists(command)) return false

    const child = spawn(command, args, { detached: true, stdio: "ignore" })
    child.on("error", () => {})
    child.unref()
    return true
  }

  const playPlatformSound = () => {
    if (process.platform === "darwin") {
      spawnSound("afplay", ["/System/Library/Sounds/Glass.aiff"])
      return
    }

    if (process.platform !== "linux" || !existsSync("/etc/arch-release")) return

    const sound = "/usr/share/sounds/freedesktop/stereo/complete.oga"
    if (existsSync(sound) && spawnSound("pw-play", [sound])) return
    if (existsSync(sound) && spawnSound("paplay", [sound])) return
    if (spawnSound("canberra-gtk-play", ["-i", "complete"])) return
    if (existsSync(sound)) spawnSound("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet", sound])
  }

  const playSound = async () => {
    const now = Date.now()
    if (now - lastSoundAt < 3000) return
    lastSoundAt = now
    playPlatformSound()
  }

  const setAttention = async () => {
    if (inTmux && targetPane) {
      await $`tmux set -wq -t ${targetPane} @opencode_attention 1`
    }
    await playSound()
  }

  const clearAttention = async () => {
    if (!inTmux || !targetPane) return
    await $`tmux set -wu -t ${targetPane} @opencode_attention`
  }

  return {
    "chat.message": async () => {
      await clearAttention()
    },

    event: async ({ event }) => {
      if (event.type === "permission.asked" || event.type === "session.error") {
        await setAttention()
        return
      }

      if (event.type === "permission.replied") {
        await clearAttention()
        return
      }

      if (event.type === "session.status" && event.properties?.status?.type === "busy") {
        await clearAttention()
      }
    },
  }
}
