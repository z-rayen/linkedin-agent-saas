import { describe, expect, it } from "vitest";
import { safeWebUrl } from "./safeUrl";

describe("links from outside", () => {
  it("keeps plain web addresses", () => {
    expect(safeWebUrl("https://jobs.example.com/1")).toBe("https://jobs.example.com/1");
    expect(safeWebUrl("http://example.com")).toBe("http://example.com/");
  });

  it("refuses script and data links, and anything that is not an address", () => {
    expect(safeWebUrl("javascript:alert(1)")).toBeNull();
    expect(safeWebUrl("data:text/html,<script>x</script>")).toBeNull();
    expect(safeWebUrl("not a link")).toBeNull();
  });
});
