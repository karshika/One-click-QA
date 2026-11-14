import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CategoryCard, ScoreRing } from "../ScoreCards";
import { Stepper } from "../Stepper";
import { Tabs } from "../Tabs";
import { StatusBadge } from "../ui";
import { fmtMs, grade, pathOf, scoreTone } from "@/lib/format";

describe("format helpers", () => {
  it("grades and tones scores", () => {
    expect(grade(91)).toBe("A");
    expect(grade(null)).toBe("n/a");
    expect(scoreTone(85)).toBe("good");
    expect(scoreTone(65)).toBe("warn");
    expect(scoreTone(10)).toBe("bad");
  });
  it("formats durations and paths", () => {
    expect(fmtMs(250)).toBe("250 ms");
    expect(fmtMs(3012)).toBe("3.0 s");
    expect(pathOf("http://x.test/a?b=1")).toBe("/a?b=1");
  });
});

describe("components", () => {
  it("ScoreRing exposes an accessible label", () => {
    render(<ScoreRing score={72.4} />);
    expect(screen.getByRole("img", { name: "Quality score 72 out of 100" })).toBeInTheDocument();
    expect(screen.getByText("Grade C")).toBeInTheDocument();
  });

  it("CategoryCard shows score and weight", () => {
    render(<CategoryCard label="API" weight={25} score={50} />);
    expect(screen.getByText("50")).toBeInTheDocument();
    expect(screen.getByText("weight 25")).toBeInTheDocument();
  });

  it("Stepper renders all stages with status text (not color alone)", () => {
    render(
      <Stepper
        stages={[
          { stage: "crawl", label: "Crawl", status: "passed", duration_ms: 1200 },
          { stage: "plan", label: "Plan", status: "degraded", duration_ms: 5 },
          { stage: "run", label: "Run", status: "running" },
        ]}
      />,
    );
    expect(screen.getByRole("list", { name: "Pipeline progress" })).toBeInTheDocument();
    expect(screen.getByText(/passed · 1.2 s/)).toBeInTheDocument();
    expect(screen.getByText(/degraded/)).toBeInTheDocument();
  });

  it("Tabs support keyboard navigation", () => {
    const onChange = vi.fn();
    render(
      <Tabs tabs={[{ key: "a", label: "A" }, { key: "b", label: "B" }]} active="a" onChange={onChange}>
        <p>panel</p>
      </Tabs>,
    );
    const tab = screen.getByRole("tab", { name: "A" });
    expect(tab).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(tab, { key: "ArrowRight" });
    expect(onChange).toHaveBeenCalledWith("b");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("panel");
  });

  it("StatusBadge includes text", () => {
    render(<StatusBadge status="failed" />);
    expect(screen.getByText("failed")).toBeInTheDocument();
  });
});
