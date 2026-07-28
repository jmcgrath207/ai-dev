import { tool } from "@opencode-ai/plugin"
import type { Plugin } from "@opencode-ai/plugin"

import {
  shortName,
  vendorAndLogical,
  resolveMatch,
  findMatches,
  readResolution,
  writeResolution,
  applyConfig,
  LOGICAL_MODELS,
  type Resolution,
  type Match,
} from "./cheap-route-lib"

const MODELS_DEV_URL = "https://models.dev/api.json"

export const CheapRoutePlugin: Plugin = async ({ client }) => {
  let resolution = readResolution()

  const toast = (title: string, message: string, variant: "info" | "warning" | "error" = "info") => {
    try {
      ;(client as any).tui?.showToast?.({ title, message, variant, duration: 6000 })
    } catch {}
  }

  return {
    config: (config: Record<string, any>) => applyConfig(config, resolution),

    "chat.message": (input: Record<string, any>, output: Record<string, any>) => {
      const model = input.model
      if (!model || model.providerID !== "cheap-route") return

      const res = readResolution()
      if (!res) {
        toast("cheap_route", "No vendor resolution. Run /cheap-route refresh to pick cheapest providers.", "warning")
        return
      }

      const parsed = vendorAndLogical(model.modelID)
      if (!parsed) return

      const match = resolveMatch(parsed.logical, parsed.vendor, res.matches)
      if (!match) return

      output.message.model = { providerID: match.providerID, modelID: match.modelID }
    },

    tool: {
      cheap_route_status: tool({
        description: "Show cheap_route model-to-vendor resolution status and available vendor options per model",
        args: {},
        execute: async (_args) => {
          const res = readResolution()
          let out = "## cheap_route Status\n\n"
          if (res && Object.keys(res.matches).length > 0) {
            out += "### Current Picks (cheapest per model)\n| Logical | Cheapest | Input $/M | Output $/M |\n|---------|----------|-----------|------------|\n"
            for (const [logical, entries] of Object.entries(res.matches)) {
              const cheap = res.cheapest[logical]
              const best = entries.find((e) => e.vendor === cheap)
              if (best) {
                out += `| ${logical} | ${best.displayVendor} (\`${best.providerID}/${best.modelID}\`) | ${best.cost.input} | ${best.cost.output} |\n`
              } else {
                out += `| ${logical} | ${cheap || "?"} | ? | ? |\n`
              }
            }
            out += `\n_Resolved: ${res.resolvedAt}_\n\n`
          } else {
            out += "_No resolution file found._\n\n"
          }

          try {
            const cat: Record<string, any> = await (await fetch(MODELS_DEV_URL)).json()
            out += "### Available Vendors by Model\n\n"
            for (const m of LOGICAL_MODELS) {
              const matches = findMatches(cat, m.patterns)
              out += `**${m.name}**: ${matches.length} options\n`
              for (const r of matches.slice(0, 6)) {
                const tag = r.input < 9999 ? `$${r.input}/${r.output}` : "no price"
                out += `- \`${r.providerID}/${r.modelID}\` — ${tag} (${r.short})\n`
              }
              if (matches.length > 6) out += `- _… and ${matches.length - 6} more_\n`
              out += "\n"
            }
          } catch {
            out += "_Could not fetch models.dev catalog._\n"
          }
          return out
        },
      }),

      cheap_route_refresh: tool({
        description: "Re-fetch models.dev pricing and re-rank cheapest providers per model. WARNING: changes vendor, busts prompt-cache stickiness — effect on NEXT session. Pass confirm=y or yes to proceed.",
        args: { confirm: tool.schema.string().describe("Type 'y' or 'yes' to confirm refresh") },
        execute: async (args) => {
          if (!["y", "yes"].includes(String(args.confirm).toLowerCase())) {
            return (
              "WARNING: Refreshing changes vendor assignments and breaks prompt-cache stickiness.\n" +
              "New picks apply to NEW sessions only (restart opencode).\n" +
              `Call again with confirm="y" or confirm="yes" to proceed.`
            )
          }

          const cat: Record<string, any> = await (await fetch(MODELS_DEV_URL)).json()
          const matches: Record<string, Match[]> = {}
          const cheapest: Record<string, string> = {}

          for (const m of LOGICAL_MODELS) {
            const found = findMatches(cat, m.patterns)
            if (found.length === 0) continue

            matches[m.id] = found.slice(0, 10).map((e) => ({
              vendor: e.providerID,
              displayVendor: e.short,
              providerID: e.providerID,
              modelID: e.modelID,
              cost: { input: e.input, output: e.output },
            }))

            cheapest[m.id] = found[0].providerID
          }

          writeResolution({ resolvedAt: new Date().toISOString(), matches, cheapest })

          let out = "## Resolution Updated\n\nCheapest picks per model:\n"
          for (const m of LOGICAL_MODELS) {
            const f = matches[m.id]?.[0]
            if (f) {
              out += `- **${m.name}** → \`${f.providerID}/${f.modelID}\` ($${f.cost.input}/${f.cost.output} /M) via ${f.displayVendor}\n`
            } else {
              out += `- **${m.name}** → no matches found\n`
            }
          }
          out += "\nAll available vendors per model will show in the model picker on next session restart.\n"
          out += "Restart opencode for picks to take effect."
          return out
        },
      }),
    },
  }
}
