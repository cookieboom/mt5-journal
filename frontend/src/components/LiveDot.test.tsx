import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import LiveDot from "./LiveDot";

describe("LiveDot", () => {
  it("shows LIVE when live", () => {
    render(<LiveDot status={{ live: true, beat_msc: 1, age_ms: 3000 }} />);
    expect(screen.getByText(/live/i)).toBeInTheDocument();
  });
  it("shows an offline hint with the journal live command when offline", () => {
    render(<LiveDot status={{ live: false, beat_msc: null, age_ms: null }} />);
    expect(screen.getByText(/journal live/i)).toBeInTheDocument();
  });
});
