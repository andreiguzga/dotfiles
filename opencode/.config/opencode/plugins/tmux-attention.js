export const TmuxAttentionPlugin = async ({ $ }) => {
  const inTmux = !!process.env.TMUX
  const targetPane = process.env.TMUX_PANE
  let lastSoundAt = 0

  const playSound = async () => {
    const now = Date.now()
    if (now - lastSoundAt < 3000) return
    lastSoundAt = now
    await $`afplay /System/Library/Sounds/Glass.aiff`
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
