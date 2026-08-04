import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import CommandsTable from "./CommandsTable";
import { CommandRow } from "../lib/types";

const base: CommandRow = {
  id: 1, position_id: 1, kind: "close", status: "done",
  symbol: null, direction: null, price_ref: null,
  sl: null, tp: null, volume: null,
  requested_msc: 1, retcode: null, retcode_name: null,
  result_volume: null, result_price: null,
  broker_comment: null, error: null,
};

describe("CommandsTable", () => {
  it("renders the symbol, not #null, for an open command with no position id yet", () => {
    render(<CommandsTable rows={[{ ...base, kind: "open", position_id: null, symbol: "XAUUSDc", direction: "buy" }]} offsetS={0} />);
    expect(screen.getByText("XAUUSDc")).toBeInTheDocument();
    expect(screen.queryByText(/null/i)).not.toBeInTheDocument();
  });

  it("still renders #position_id for non-open commands", () => {
    render(<CommandsTable rows={[{ ...base, position_id: 42 }]} offsetS={0} />);
    expect(screen.getByText("#42")).toBeInTheDocument();
  });
});
