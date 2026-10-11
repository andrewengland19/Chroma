import { expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import App from "./App";

test("renders the empty app shell", () => {
  render(<App />);
  expect(screen.getByTestId("app-root")).toBeInTheDocument();
});
