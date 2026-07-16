import { render, screen } from "@testing-library/react";

import HomePage from "./page";

describe("HomePage", () => {
  it("introduces the evidence-first product", () => {
    render(<HomePage />);

    expect(
      screen.getByRole("heading", { name: /document intelligence that proves its answers/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/ஆதாரத்துடன் பதில்/)).toHaveAttribute("lang", "ta");
  });

  it("renders explicit uncertain states", () => {
    render(<HomePage />);

    expect(screen.getByText("Not enough evidence")).toBeInTheDocument();
    expect(screen.getByText("Partial support")).toBeInTheDocument();
    expect(screen.getByText("Processing error")).toBeInTheDocument();
  });
});
