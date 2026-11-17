import type { ComponentProps } from "react";

export function Checkbox(props: Omit<ComponentProps<"input">, "type">) {
  return <input type="checkbox" {...props} />;
}
