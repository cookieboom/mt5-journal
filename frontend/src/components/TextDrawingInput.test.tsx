import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import TextDrawingInput from "./TextDrawingInput";
import { MAX_TEXT_LEN } from "../lib/drawings";

describe("TextDrawingInput", () => {
  it("renders focused with the initial value", () => {
    const { getByTestId } = render(
      <TextDrawingInput x={10} y={20} initial="old" onCommit={() => {}} onCancel={() => {}} />,
    );
    const input = getByTestId("text-drawing-input") as HTMLInputElement;
    expect(input.value).toBe("old");
    expect(document.activeElement).toBe(input);
  });

  it("commits the trimmed value on Enter", () => {
    const onCommit = vi.fn();
    const { getByTestId } = render(
      <TextDrawingInput x={0} y={0} initial="" onCommit={onCommit} onCancel={() => {}} />,
    );
    const input = getByTestId("text-drawing-input");
    fireEvent.change(input, { target: { value: "  supply zone  " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onCommit).toHaveBeenCalledWith("supply zone");
  });

  it("cancels on Escape without committing", () => {
    const onCommit = vi.fn();
    const onCancel = vi.fn();
    const { getByTestId } = render(
      <TextDrawingInput x={0} y={0} initial="" onCommit={onCommit} onCancel={onCancel} />,
    );
    const input = getByTestId("text-drawing-input");
    fireEvent.change(input, { target: { value: "note" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("commits the trimmed value on blur (click-away-to-save)", () => {
    const onCommit = vi.fn();
    const { getByTestId } = render(
      <TextDrawingInput x={0} y={0} initial="" onCommit={onCommit} onCancel={() => {}} />,
    );
    const input = getByTestId("text-drawing-input");
    fireEvent.change(input, { target: { value: "note" } });
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledWith("note");
  });

  it("caps the input at MAX_TEXT_LEN", () => {
    const { getByTestId } = render(
      <TextDrawingInput x={0} y={0} initial="" onCommit={() => {}} onCancel={() => {}} />,
    );
    expect((getByTestId("text-drawing-input") as HTMLInputElement).maxLength).toBe(MAX_TEXT_LEN);
  });

  // Enter (and Escape) commit/cancel, then the parent unmounts this input —
  // and in real browsers (not jsdom, which doesn't reproduce this) removing a
  // focused node fires a trailing "blur" on it. Without a guard, that blur
  // would re-run onCommit/onCancel a second time against the same value.
  // These two simulate that trailing blur explicitly, since jsdom won't
  // produce it on its own.
  it("Enter commits once — a trailing blur on the same input does not commit again", () => {
    const onCommit = vi.fn();
    const { getByTestId } = render(
      <TextDrawingInput x={0} y={0} initial="" onCommit={onCommit} onCancel={() => {}} />,
    );
    const input = getByTestId("text-drawing-input");
    fireEvent.change(input, { target: { value: "hello" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onCommit).toHaveBeenCalledTimes(1);
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledTimes(1);
  });

  it("Escape cancels once — a trailing blur does not also commit", () => {
    const onCommit = vi.fn();
    const onCancel = vi.fn();
    const { getByTestId } = render(
      <TextDrawingInput x={0} y={0} initial="" onCommit={onCommit} onCancel={onCancel} />,
    );
    const input = getByTestId("text-drawing-input");
    fireEvent.change(input, { target: { value: "hello" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
    fireEvent.blur(input);
    expect(onCommit).not.toHaveBeenCalled();
  });
});
