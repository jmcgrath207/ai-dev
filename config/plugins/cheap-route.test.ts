import { describe, it, before, after } from "node:test"
import { strict as assert } from "node:assert"
import { mkdtempSync, writeFileSync, rmSync } from "fs"
import { join } from "path"
import { tmpdir } from "os"

import {
  shortName,
  vendorAndLogical,
  resolveMatch,
  findMatches,
  readResolution,
  writeResolution,
  applyConfig,
  EXCLUDED,
  LOGICAL_MODELS,
  type Match,
  type Resolution,
  type CatalogEntry,
} from "./cheap-route-lib"

describe("shortName", () => {
  it("capitalizes first letter of single word", () => {
    assert.equal(shortName("deepinfra"), "Deepinfra")
  })

  it("capitalizes each hyphenated segment", () => {
    assert.equal(shortName("fireworks"), "Fireworks")
  })

  it("strips -cn suffix", () => {
    assert.equal(shortName("siliconflow-cn"), "Siliconflow")
  })

  it("strips -api suffix", () => {
    assert.equal(shortName("groq-api"), "Groq")
  })

  it("handles multi-segment names with lowercase internal", () => {
    assert.equal(shortName("azure-openai"), "Azure Openai")
  })

  it("strips -plan", () => {
    assert.equal(shortName("nebius-plan"), "Nebius")
  })

  it("strips -ai", () => {
    assert.equal(shortName("openrouter-ai"), "Openrouter")
  })
})

describe("vendorAndLogical", () => {
  it("splits on first slash", () => {
    assert.deepEqual(vendorAndLogical("deepinfra/deepseek-v4-flash"), { vendor: "deepinfra", logical: "deepseek-v4-flash" })
  })

  it("handles multi-segment model ID", () => {
    assert.deepEqual(vendorAndLogical("fireworks/deepseek/deepseek-v4-flash"), { vendor: "fireworks", logical: "deepseek/deepseek-v4-flash" })
  })

  it("returns null for single segment (no slash)", () => {
    assert.equal(vendorAndLogical("no-slash"), null)
  })

  it("returns null for empty string", () => {
    assert.equal(vendorAndLogical(""), null)
  })

  it("parses vendor with dots", () => {
    assert.deepEqual(vendorAndLogical("my.provider/glm-5.2"), { vendor: "my.provider", logical: "glm-5.2" })
  })
})

describe("resolveMatch", () => {
  const matchA: Match = { vendor: "deepinfra", displayVendor: "DeepInfra", providerID: "deepinfra", modelID: "deepseek-ai/DeepSeek-V4-Flash", cost: { input: 0.09, output: 0.18 } }
  const matchB: Match = { vendor: "fireworks", displayVendor: "Fireworks", providerID: "fireworks-ai", modelID: "accounts/fireworks/models/deepseek-v4-flash", cost: { input: 0.14, output: 0.28 } }

  const matches: Record<string, Match[]> = {
    "deepseek-v4-flash": [matchA, matchB],
  }

  it("finds match by vendor exact case", () => {
    const r = resolveMatch("deepseek-v4-flash", "deepinfra", matches)
    assert.equal(r, matchA)
  })

  it("finds match case-insensitively", () => {
    const r = resolveMatch("deepseek-v4-flash", "DeepInfra", matches)
    assert.equal(r, matchA)
  })

  it("finds second vendor", () => {
    const r = resolveMatch("deepseek-v4-flash", "fireworks", matches)
    assert.equal(r, matchB)
  })

  it("returns null for unknown logical", () => {
    assert.equal(resolveMatch("nonexistent", "deepinfra", matches), null)
  })

  it("returns null for unknown vendor", () => {
    assert.equal(resolveMatch("deepseek-v4-flash", "unknown", matches), null)
  })

  it("returns null for empty matches", () => {
    assert.equal(resolveMatch("deepseek-v4-flash", "deepinfra", {}), null)
  })
})

describe("findMatches", () => {
  const sample: Record<string, any> = {
    deepinfra: {
      models: {
        "deepseek-ai/DeepSeek-V4-Flash": { name: "DeepSeek V4 Flash", cost: { input: 0.09, output: 0.18 } },
      },
    },
    "fireworks-ai": {
      models: {
        "accounts/fireworks/models/deepseek-v4-flash": { name: "deepseek-v4-flash", cost: { input: 0.14, output: 0.28 } },
      },
    },
    openrouter: {
      models: {
        "deepseek/deepseek-v4-flash": { name: "DeepSeek V4 Flash", cost: { input: 0.5, output: 1.0 } },
      },
    },
  }

  it("finds matching models", () => {
    const results = findMatches(sample, ["deepseek-v4-flash"])
    assert.equal(results.length, 2)
  })

  it("excludes OpenRouter", () => {
    const results = findMatches(sample, ["deepseek-v4-flash"])
    assert.ok(!results.some((r) => EXCLUDED.has(r.providerID)), "openrouter should be excluded")
    assert.equal(results.find((r) => r.providerID === "openrouter"), undefined)
  })

  it("sorts by combined price ascending", () => {
    const results = findMatches(sample, ["deepseek-v4-flash"])
    for (let i = 1; i < results.length; i++) {
      assert.ok(results[i - 1].input + results[i - 1].output <= results[i].input + results[i].output, "should be sorted by total price")
    }
  })

  it("returns empty for no match", () => {
    assert.equal(findMatches(sample, ["nonexistent"]).length, 0)
  })

  it("defaults missing cost to 9999", () => {
    const cat: Record<string, any> = {
      "new-provider": {
        models: {
          "test-model": { name: "Test Model" },
        },
      },
    }
    const results = findMatches(cat, ["test-model"])
    assert.equal(results.length, 1)
    assert.equal(results[0].input, 9999)
    assert.equal(results[0].output, 9999)
  })

  it("sets short name from providerID", () => {
    const results = findMatches(sample, ["deepseek-v4-flash"])
    const d = results.find((r) => r.providerID === "deepinfra")
    assert.equal(d?.short, "Deepinfra")
  })

  it("skips providers without models key", () => {
    const cat: Record<string, any> = {
      emptyprov: {},
    }
    assert.equal(findMatches(cat, ["test"]).length, 0)
  })

  it("skips provider entries that are not objects", () => {
    const cat: Record<string, any> = {
      badprov: "string",
    }
    assert.equal(findMatches(cat, ["test"]).length, 0)
  })
})

describe("readResolution and writeResolution", () => {
  let tmpDir: string
  const origEnv = process.env.CHEAP_ROUTE_CACHE_DIR

  before(() => {
    tmpDir = mkdtempSync(join(tmpdir(), "crtest-"))
    process.env.CHEAP_ROUTE_CACHE_DIR = tmpDir
  })

  after(() => {
    if (origEnv === undefined) delete process.env.CHEAP_ROUTE_CACHE_DIR
    else process.env.CHEAP_ROUTE_CACHE_DIR = origEnv
    rmSync(tmpDir, { recursive: true, force: true })
  })

  it("returns null when resolution file does not exist", () => {
    assert.equal(readResolution(), null)
  })

  it("returns null for invalid JSON file", () => {
    writeFileSync(join(tmpDir, "resolution.json"), "not-json", "utf-8")
    assert.equal(readResolution(), null)
  })

  it("roundtrips resolution data", () => {
    const data: Resolution = {
      resolvedAt: "2026-07-26T12:00:00.000Z",
      matches: {
        "deepseek-v4-flash": [
          { vendor: "deepinfra", displayVendor: "DeepInfra", providerID: "deepinfra", modelID: "deepseek-ai/DeepSeek-V4-Flash", cost: { input: 0.09, output: 0.18 } },
        ],
      },
      cheapest: { "deepseek-v4-flash": "deepinfra" },
    }
    writeResolution(data)
    const read = readResolution()
    assert.notEqual(read, null)
    assert.deepEqual(read?.resolvedAt, data.resolvedAt)
    assert.deepEqual(read?.matches, data.matches)
    assert.deepEqual(read?.cheapest, data.cheapest)
    assert.equal(read?.matches["deepseek-v4-flash"]?.length, 1)
    assert.equal(read?.matches["deepseek-v4-flash"]?.[0]?.vendor, "deepinfra")
  })

  it("clears cacheDir env override after test", () => {
    const dir = process.env.CHEAP_ROUTE_CACHE_DIR
    if (origEnv === undefined) {
      assert.equal(dir, tmpDir, "should be set to tmpDir")
    }
  })
})

describe("applyConfig", () => {
  const sampleResolution: Resolution = {
    resolvedAt: "2026-07-26T12:00:00.000Z",
    matches: {
      "deepseek-v4-flash": [
        { vendor: "deepinfra", displayVendor: "DeepInfra", providerID: "deepinfra", modelID: "DeepSeek-V4-Flash", cost: { input: 0.09, output: 0.18 } },
        { vendor: "fireworks", displayVendor: "Fireworks", providerID: "fireworks", modelID: "DeepSeek-V4-Flash", cost: { input: 0.14, output: 0.28 } },
      ],
      "glm-5.2": [
        { vendor: "deepinfra", displayVendor: "DeepInfra", providerID: "deepinfra", modelID: "GLM-5.2", cost: { input: 1.0, output: 2.0 } },
      ],
    },
    cheapest: { "deepseek-v4-flash": "deepinfra", "glm-5.2": "deepinfra" },
  }

  it("does nothing when resolution is null", () => {
    const config: Record<string, any> = {}
    applyConfig(config, null)
    assert.ok(!config.provider?.["cheap-route"])
  })

  it("does nothing when resolution has empty matches", () => {
    const config: Record<string, any> = {}
    applyConfig(config, { resolvedAt: "...", matches: {}, cheapest: {} })
    assert.ok(!config.provider?.["cheap-route"])
  })

  it("does nothing when resolution has empty cheapest", () => {
    const config: Record<string, any> = {}
    applyConfig(config, { resolvedAt: "...", matches: { "deepseek-v4-flash": [] }, cheapest: {} })
    assert.ok(!config.provider?.["cheap-route"])
  })

  it("registers cheap-route provider with all vendor combos", () => {
    const config: Record<string, any> = {}
    applyConfig(config, sampleResolution)
    assert.ok(config.provider["cheap-route"])
    assert.ok(config.provider["cheap-route"].models["deepinfra/deepseek-v4-flash"])
    assert.ok(config.provider["cheap-route"].models["fireworks/deepseek-v4-flash"])
    assert.ok(config.provider["cheap-route"].models["deepinfra/glm-5.2"])
    assert.equal(Object.keys(config.provider["cheap-route"].models).length, 3)
  })

  it("sets small_model to cheapest deepseek-v4-flash", () => {
    const config: Record<string, any> = {}
    applyConfig(config, sampleResolution)
    assert.equal(config.small_model, "cheap-route/deepinfra/deepseek-v4-flash")
  })

  it("does not set small_model when deepseek not in cheapest", () => {
    const config: Record<string, any> = {}
    applyConfig(config, {
      resolvedAt: "...",
      matches: { "glm-5.2": [{ vendor: "deepinfra", displayVendor: "DeepInfra", providerID: "deepinfra", modelID: "GLM-5.2", cost: { input: 1.0, output: 2.0 } }] },
      cheapest: { "glm-5.2": "deepinfra" },
    })
    assert.ok(!config.small_model)
  })

  it("sets agent model when not previously set", () => {
    const config: Record<string, any> = { agent: {} }
    applyConfig(config, sampleResolution)
    assert.equal(config.agent.plan.model, "cheap-route/deepinfra/glm-5.2")
  })

  it("does not overwrite existing agent model", () => {
    const config: Record<string, any> = { agent: { plan: { model: "my-custom-model" } } }
    applyConfig(config, sampleResolution)
    assert.equal(config.agent.plan.model, "my-custom-model")
    assert.equal(config.agent.explore.model, "cheap-route/deepinfra/deepseek-v4-flash")
  })

  it("skips agent model when model not in cheapest", () => {
    const config: Record<string, any> = { agent: {} }
    applyConfig(config, {
      resolvedAt: "...",
      matches: { "deepseek-v4-flash": [{ vendor: "deepinfra", displayVendor: "DeepInfra", providerID: "deepinfra", modelID: "DeepSeek-V4-Flash", cost: { input: 0.09, output: 0.18 } }] },
      cheapest: { "deepseek-v4-flash": "deepinfra" },
    })
    assert.ok(!config.agent.plan?.model)  // glm-5.2 not in cheapest
  })

  it("skips registering provider when all match lists are empty", () => {
    const config: Record<string, any> = {}
    applyConfig(config, {
      resolvedAt: "...",
      matches: { "deepseek-v4-flash": [], "glm-5.2": [] },
      cheapest: {},
    })
    assert.ok(!config.provider?.["cheap-route"])
  })
})
