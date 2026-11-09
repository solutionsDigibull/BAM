import { cleanup, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, test } from "vitest";

import { HostSlot, useSlotElement } from "./slots";

afterEach(cleanup);

function Probe() {
  const el = useSlotElement("scope");
  return <p data-testid="probe">{el ? `slot:${el.getAttribute("data-slot")}` : "no slot"}</p>;
}

describe("a screen the host renders into the workbench has a slot to render into (#713)", () => {
  test("test_713_the_slot_element_is_available_while_the_slot_is_on_show", () => {
    render(
      <>
        <HostSlot id="scope" />
        <Probe />
      </>,
    );
    expect(screen.getByTestId("probe").textContent).toBe("slot:scope");
  });

  test("test_713_the_slot_is_released_when_the_screen_changes", () => {
    function Toggle() {
      const [on, setOn] = useState(true);
      return (
        <>
          <button onClick={() => setOn(false)}>leave</button>
          {on && <HostSlot id="scope" />}
          <Probe />
        </>
      );
    }
    render(<Toggle />);
    expect(screen.getByTestId("probe").textContent).toBe("slot:scope");
    screen.getByText("leave").click();
    return Promise.resolve().then(() => expect(screen.getByTestId("probe").textContent).toBe("no slot"));
  });
});
