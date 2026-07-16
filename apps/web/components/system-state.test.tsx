import { render, screen } from "@testing-library/react";

import { SystemState } from "./system-state";

describe("SystemState", () => {
  it("announces loading updates politely", () => {
    render(<SystemState state="loading" title="Checking evidence" description="Please wait." />);

    expect(screen.getByRole("article")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByText("Working")).toBeInTheDocument();
  });
});
