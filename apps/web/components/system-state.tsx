type SystemStateKind = "empty" | "error" | "loading" | "partial" | "refusal";

type SystemStateProps = Readonly<{
  description: string;
  state: SystemStateKind;
  title: string;
}>;

const stateLabels: Record<SystemStateKind, string> = {
  empty: "No results",
  error: "Action required",
  loading: "Working",
  partial: "Review evidence",
  refusal: "Evidence required",
};

export function SystemState({ description, state, title }: SystemStateProps) {
  return (
    <article
      className="system-state"
      data-state={state}
      aria-live={state === "loading" ? "polite" : "off"}
    >
      <p className="state-label">{stateLabels[state]}</p>
      <h3>{title}</h3>
      <p>{description}</p>
    </article>
  );
}
