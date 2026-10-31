// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ModeMenu from "./ModeMenu";

afterEach(cleanup);

function Menu({ select = vi.fn(), disabled = false }: { select?: (mode: string) => void; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  return <><ModeMenu mode="code" disabled={disabled} open={open} onOpenChange={setOpen} onSelect={select} />
    <button>外部按钮</button></>;
}

describe("mode menu keyboard and dismissal", () => {
  it("opens with the current item focused, navigates without selecting, and restores focus on Escape", async () => {
    const select = vi.fn();
    const user = userEvent.setup();
    render(<Menu select={select} />);
    const trigger = screen.getByRole("button", { name: "编程模式" });
    await user.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(document.activeElement).toBe(screen.getByRole("menuitemradio", { name: /编程模式/ }));
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(screen.getByRole("menuitemradio", { name: /聊天模式/ }));
    await user.keyboard("{End}");
    expect(document.activeElement).toBe(screen.getByRole("menuitemradio", { name: /编程模式/ }));
    await user.keyboard("{Home}{ArrowDown}");
    expect(document.activeElement).toBe(screen.getByRole("menuitemradio", { name: /计划模式/ }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
    expect(select).not.toHaveBeenCalled();
  });

  it("opens with arrows or Space and activates a choice with Enter or Space", async () => {
    const select = vi.fn();
    const user = userEvent.setup();
    render(<Menu select={select} />);
    const trigger = screen.getByRole("button", { name: "编程模式" });
    trigger.focus();
    await user.keyboard("{ArrowUp}");
    expect(document.activeElement).toBe(screen.getByRole("menuitemradio", { name: /编程模式/ }));
    await user.keyboard("{ArrowUp}{Enter}");
    expect(select).toHaveBeenLastCalledWith("plan");
    expect(document.activeElement).toBe(trigger);
    await user.keyboard(" ");
    expect(screen.getByRole("menu")).toBeTruthy();
    await user.keyboard("{Home} ");
    expect(select).toHaveBeenLastCalledWith("chat");
    expect(screen.queryByRole("menu")).toBeNull();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("menu")).toBeTruthy();
  });

  it("dismisses outside without stealing focus and closes when disabled", async () => {
    const select = vi.fn();
    const user = userEvent.setup();
    const view = render(<Menu select={select} />);
    await user.click(screen.getByRole("button", { name: "编程模式" }));
    const outside = screen.getByRole("button", { name: "外部按钮" });
    await user.click(outside);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(outside);
    await user.click(screen.getByRole("button", { name: "编程模式" }));
    view.rerender(<Menu select={select} disabled />);
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "编程模式" }));
    expect(select).not.toHaveBeenCalled();
  });
});
