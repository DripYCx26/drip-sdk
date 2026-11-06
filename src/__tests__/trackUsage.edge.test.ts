import { describe, it, expect, vi, afterEach } from "vitest"
import { Drip } from "../index.js"

function createDrip() {
  return new Drip({ apiKey: "sk_test_dummy" })
}

// ✅ helper to enforce "fail before network"
async function expectValidationFailure(payload: any, message?: string) {
  const drip = createDrip()
  const requestSpy = vi.spyOn(drip as any, "request")

  const assertion = expect(drip.trackUsage(payload)).rejects

  if (message) {
    await assertion.toThrow(message)
  } else {
    await assertion.toThrow()
  }

  expect(requestSpy).not.toHaveBeenCalled()
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe("trackUsage validation", () => {

  it("should fail with empty customerId", async () => {
    await expectValidationFailure({
      customerId: "",
      meter: "tokens",
      quantity: 100,
    })
  })

  it("should fail with invalid customerId type", async () => {
    await expectValidationFailure({
      customerId: {} as any,
      meter: "tokens",
      quantity: 100,
    })
  })

  it("should fail with empty meter", async () => {
    await expectValidationFailure({
      customerId: "user_1",
      meter: "",
      quantity: 100,
    })
  })

  it("should fail with whitespace meter", async () => {
    await expectValidationFailure({
      customerId: "user_1",
      meter: "   ",
      quantity: 100,
    })
  })

  it("should fail with negative quantity", async () => {
    await expectValidationFailure(
      {
        customerId: "user_1",
        meter: "tokens",
        quantity: -100,
      },
      "quantity must be a finite, non-negative number"
    )
  })

  it("should allow zero quantity (wrapApiCall extractUsage fallback)", async () => {
    const drip = createDrip()
    const requestSpy = vi
      .spyOn(drip as any, "request")
      .mockResolvedValue({ success: true } as any)

    await drip.trackUsage({ customerId: "user_1", meter: "tokens", quantity: 0 })

    expect(requestSpy).toHaveBeenCalledTimes(1)
  })

  it("should fail with NaN quantity", async () => {
    await expectValidationFailure({
      customerId: "user_1",
      meter: "tokens",
      quantity: NaN,
    })
  })

  it("should fail with Infinity quantity", async () => {
    await expectValidationFailure({
      customerId: "user_1",
      meter: "tokens",
      quantity: Infinity,
    })
  })

  it("should fail with invalid mode", async () => {
    await expectValidationFailure(
      {
        customerId: "user_1",
        meter: "tokens",
        quantity: 100,
        mode: "invalid_mode",
      },
      "invalid mode"
    )
  })

  it("should fail with null inputs", async () => {
    await expectValidationFailure({
      customerId: null,
      meter: null,
      quantity: null,
    })
  })

  it("should fail with undefined inputs", async () => {
    await expectValidationFailure({
      customerId: undefined,
      meter: undefined,
      quantity: undefined,
    })
  })

})
