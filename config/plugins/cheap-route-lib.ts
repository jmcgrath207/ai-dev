import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs"
import { homedir } from "os"
import { join } from "path"

export type Match = {
  vendor: string
  displayVendor: string
  providerID: string
  modelID: string
  cost: { input: number; output: number }
}

export type Resolution = {
  resolvedAt: string
  matches: Record<string, Match[]>
  cheapest: Record<string, string>
}

export type LogicalModel = { id: string; name: string; patterns: string[] }
export type CatalogEntry = { providerID: string; modelID: string; input: number; output: number; short: string }

export const LOGICAL_MODELS: LogicalModel[] = [
  { id: "deepseek-v4-flash", name: "DeepSeek V4 Flash", patterns: ["deepseek-v4-flash", "DeepSeek-V4-Flash"] },
  { id: "glm-5.2", name: "GLM 5.2", patterns: ["glm-5.2", "GLM-5.2", "glm-5p2"] },
  { id: "kimi-k3", name: "Kimi K3", patterns: ["kimi-k3", "Kimi-K3"] },
  { id: "minimax-m3", name: "MiniMax M3", patterns: ["minimax-m3", "MiniMax-M3"] },
]

export const EXCLUDED = new Set(["openrouter", "opencode", "opencode-go", "opencode-go-rc"])

export function shortName(pid: string): string {
  return pid
    .replace(/-(cn|api|plan|ai)$/, "")
    .split("-")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join(" ")
}

export function vendorAndLogical(modelID: string): { vendor: string; logical: string } | null {
  const idx = modelID.indexOf("/")
  if (idx === -1) return null
  return { vendor: modelID.slice(0, idx), logical: modelID.slice(idx + 1) }
}

export function resolveMatch(logical: string, vendor: string, matches: Record<string, Match[]>): Match | null {
  const entries = matches[logical]
  if (!entries) return null
  return entries.find((e) => e.vendor.toLowerCase() === vendor.toLowerCase()) ?? null
}

export function findMatches(catalog: Record<string, any>, patterns: string[]): CatalogEntry[] {
  const results: CatalogEntry[] = []
  for (const [pid, prov] of Object.entries(catalog)) {
    if (EXCLUDED.has(pid)) continue
    if (!prov || typeof prov !== "object") continue
    const models = (prov as Record<string, any>).models ?? {}
    for (const [mid, model] of Object.entries(models)) {
      const fullId = `${pid}/${mid}`
      const modelName = (model as Record<string, any>).name ?? ""
      if (!patterns.some((p) => fullId.toLowerCase().includes(p.toLowerCase()) || modelName.toLowerCase().includes(p.toLowerCase()))) continue
      const cost = (model as Record<string, any>).cost ?? {}
      results.push({
        providerID: pid,
        modelID: mid,
        input: typeof cost.input === "number" ? cost.input : 9999,
        output: typeof cost.output === "number" ? cost.output : 9999,
        short: shortName(pid),
      })
    }
  }
  results.sort((a, b) => a.input + a.output - (b.input + b.output))
  return results
}

export function cacheDir(): string {
  return process.env.CHEAP_ROUTE_CACHE_DIR ?? join(homedir(), ".cache", "cheap-route")
}

export function resolutionFile(): string {
  return join(cacheDir(), "resolution.json")
}

export function readResolution(): Resolution | null {
  try {
    const f = resolutionFile()
    if (existsSync(f)) return JSON.parse(readFileSync(f, "utf-8"))
  } catch {}
  return null
}

export function writeResolution(data: Resolution): void {
  const dir = cacheDir()
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, "resolution.json"), JSON.stringify(data, null, 2), "utf-8")
}

export const AGENT_DEFAULTS: Record<string, string> = {
  plan: "glm-5.2",
  explore: "deepseek-v4-flash",
  scout: "deepseek-v4-flash",
  build: "minimax-m3",
}

const SMALL_MODEL_LOGICAL = "deepseek-v4-flash"

export function applyConfig(config: Record<string, any>, resolution: Resolution | null): void {
  if (!resolution || Object.keys(resolution.matches).length === 0 || Object.keys(resolution.cheapest).length === 0) {
    return
  }
  config.provider ??= {}
  const models: Record<string, any> = {}
  for (const logical of Object.keys(resolution.matches)) {
    const entryList = resolution.matches[logical]
    if (entryList.length === 0) continue
    for (const entry of entryList) {
      models[`${entry.vendor}/${logical}`] = {
        name: `${LOGICAL_MODELS.find((m) => m.id === logical)?.name || logical} (${entry.displayVendor})`,
        tool_call: true,
      }
    }
  }
  if (Object.keys(models).length === 0) return
  config.provider["cheap-route"] = {
    npm: "@ai-sdk/openai-compatible",
    name: "cheap_route",
    options: { baseURL: "http://127.0.0.1:0/na" },
    models,
  }
  const cheapest = resolution.cheapest
  config.agent ??= {}
  for (const [agent, logical] of Object.entries(AGENT_DEFAULTS)) {
    if (!config.agent[agent]) config.agent[agent] = {}
    if (!config.agent[agent].model && cheapest[logical]) {
      config.agent[agent].model = `cheap-route/${cheapest[logical]}/${logical}`
    }
  }
  if (cheapest[SMALL_MODEL_LOGICAL]) {
    config.small_model = `cheap-route/${cheapest[SMALL_MODEL_LOGICAL]}/${SMALL_MODEL_LOGICAL}`
  }
}
