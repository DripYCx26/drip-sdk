import { describe, it, expect } from "vitest"
import Drip from "../index.js"

describe("Drip SDK basic", () => {
  it("should initialize client with api key", () => {
    const drip = new Drip({ apiKey: "sk_test_dummy" })
    expect(drip).toBeDefined()
  })

  it("should expose keyType correctly", () => {
    const drip = new Drip({ apiKey: "sk_test_dummy" })
    expect(drip.keyType).toBe("secret")
  })
})