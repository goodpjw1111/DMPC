// Per-problem in-browser simulator registry.
//
// The backend stores a `simulator_key` on every problem (from the problem module's
// META). The client looks that key up here to decide which interactive simulator to
// render. A problem whose key is null/unknown simply has NO in-browser simulator —
// the UI falls back to plain output/code submission. (The server is always the sole
// authority for scoring; the browser simulator is a convenience/preview only.)
//
// Adding a new problem's simulator is purely additive:
//     registerSimulator("my_key", { Step: MyStepSim, Challenge: MyChallengeSim });
// No edits to the dispatch site (ApiProblemView) are needed. This module deliberately
// does NOT import any component (components register INTO it) so there is no cycle.

import type { ComponentType } from "react";
import type { Mission } from "./mock";

// Props the Step Up simulator receives (matches the clean-robot StepSimulator).
export type StepSimProps = {
  mission: number;
  setMission: (i: number) => void;
  onOutput: (s: string) => void;
  missions: Mission[];
  initial: string;
};

export type SimulatorEntry = {
  Step?: ComponentType<StepSimProps>;            // Step Up: interactive editor → output
  Challenge?: ComponentType<Record<string, never>>; // Challenge: paste-in input/output visualizer
};

const REGISTRY: Record<string, SimulatorEntry> = {};

export function registerSimulator(key: string, entry: SimulatorEntry): void {
  REGISTRY[key] = entry;
}

export function getSimulator(key: string | null | undefined): SimulatorEntry | null {
  if (!key) return null;
  return REGISTRY[key] ?? null;
}
