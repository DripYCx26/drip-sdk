import { describe, it, expect, vi } from "vitest"
import { Drip } from "../index.js"

function createDrip() {
  return new Drip({ apiKey: "sk_test_dummy" })
}

describe("trackUsage worst-case inputs", () => {

  it("should fail with empty customerId", async () => {
    const drip = createDrip()
    await expect(
      drip.trackUsage({
        customerId: "",
        meter: "tokens",
        quantity: 100,
      } as any)
    ).rejects.toThrow()
  })

  it("should fail with empty meter", async () => {
    const drip = createDrip()
    await expect(
      drip.trackUsage({
        customerId: "user_1",
        meter: "",
        quantity: 100,
      } as any)
    ).rejects.toThrow()
  })

  it("should fail with negative quantity", async () => {
    const drip = createDrip()
    await expect(
      drip.trackUsage({
        customerId: "user_1",
        meter: "tokens",
        quantity: -100,
      } as any)
    ).rejects.toThrow()
  })

  it("should fail with zero quantity", async () => {
    const drip = createDrip()
    await expect(
      drip.trackUsage({
        customerId: "user_1",
        meter: "tokens",
        quantity: 0,
      } as any)
    ).rejects.toThrow()
  })

  it("should fail with invalid mode", async () => {
    const drip = createDrip()
    await expect(
      drip.trackUsage({
        customerId: "user_1",
        meter: "tokens",
        quantity: 100,
        mode: "invalid_mode",
      } as any)
    ).rejects.toThrow()
  })

  it("should fail with extremely large quantity", async () => {
    const drip = createDrip()
    await expect(
      drip.trackUsage({
        customerId: "user_1",
        meter: "tokens",
        quantity: Number.MAX_SAFE_INTEGER,
      } as any)
    ).rejects.toThrow()
  })

  it("should fail with null inputs", async () => {
    const drip = createDrip()

    await expect(
      drip.trackUsage({
        customerId: null,
        meter: null,
        quantity: null,
      } as any)
    ).rejects.toThrow()
  })

  it("should fail with undefined inputs", async () => {
    const drip = createDrip()
    await expect(
      drip.trackUsage({
        customerId: undefined,
        meter: undefined,
        quantity: undefined,
      } as any)
    ).rejects.toThrow()
  })

  it("should fail with object instead of string", async () => {
    const drip = createDrip()
    await expect(
      drip.trackUsage({
        customerId: {} as any,
        meter: [] as any,
        quantity: 100,
      })
    ).rejects.toThrow()
  })

  it("should fail when metadata contains circular reference", async () => {
    const drip = createDrip()
    const circular: any = {}
    circular.self = circular

    await expect(


      drip.trackUsage({
        customerId: "user_1",
        meter: "tokens",
        quantity: 100,
        metadata: circular,
      })
    ).rejects.toThrow()
  })

})
it("should throw clear error for negative quantity", async () => {
  const drip = createDrip()
  const requestSpy = vi.spyOn(drip as any, "request")

  await expect(
    drip.trackUsage({
      customerId: "user",
      meter: "tokens",
      quantity: -1,
    })
  ).rejects.toThrow("quantity must be a positive number")

  expect(requestSpy).not.toHaveBeenCalled()
})
