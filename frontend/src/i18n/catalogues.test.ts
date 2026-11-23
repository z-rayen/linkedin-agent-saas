import { describe, expect, it } from "vitest";
import { en } from "./en";
import { fr } from "./fr";

type Tree = { readonly [key: string]: string | Tree };

/** Every dotted key of a catalogue, and the placeholders each message uses. */
function entries(tree: Tree, prefix = ""): Map<string, string> {
  const result = new Map<string, string>();
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix === "" ? key : `${prefix}.${key}`;
    if (typeof value === "string") result.set(path, value);
    else for (const [nested, text] of entries(value, path)) result.set(nested, text);
  }
  return result;
}

function placeholders(text: string): string[] {
  return [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1] ?? "").sort();
}

const frenchKeys = entries(fr);
const englishKeys = entries(en);

describe("the French and English catalogues", () => {
  it("have exactly the same keys, so no screen can show a missing or a raw key", () => {
    expect([...englishKeys.keys()].filter((key) => !frenchKeys.has(key))).toEqual([]);
    expect([...frenchKeys.keys()].filter((key) => !englishKeys.has(key))).toEqual([]);
  });

  it("use the same placeholders in every language of a message", () => {
    for (const [key, french] of frenchKeys) {
      expect(placeholders(englishKeys.get(key) ?? ""), key).toEqual(placeholders(french));
    }
  });

  it("have no empty message", () => {
    for (const [key, text] of [...frenchKeys, ...englishKeys]) {
      expect(text.trim(), key).not.toBe("");
    }
  });
});
