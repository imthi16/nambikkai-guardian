import { SystemState } from "../components/system-state";

const capabilities = [
  {
    title: "Multilingual by design",
    description: "Tamil, Tanglish, and English remain first-class query and document languages.",
  },
  {
    title: "Evidence before answers",
    description: "Page-level evidence and claim verification determine whether an answer is safe.",
  },
  {
    title: "Untrusted documents",
    description: "Retrieved text is treated as data and never as an instruction to the system.",
  },
];

export default function HomePage() {
  return (
    <main id="main-content">
      <section className="hero" aria-labelledby="hero-title">
        <p className="eyebrow">NAMBIKKAI GUARDIAN</p>
        <h1 id="hero-title">Document intelligence that proves its answers.</h1>
        <p className="hero-copy">
          A secure foundation for evidence-grounded answers across Tamil, Tanglish, and English
          documents. Unsupported answers are refused, not improvised.
        </p>
        <p lang="ta" className="tamil-sample">
          ஆதாரத்துடன் பதில். ஆதாரம் இல்லையெனில் மறுப்பு.
        </p>
      </section>

      <section className="capabilities" aria-labelledby="capabilities-title">
        <div>
          <p className="section-kicker">TRUST MODEL</p>
          <h2 id="capabilities-title">Safety is part of the architecture.</h2>
        </div>
        <div className="capability-grid">
          {capabilities.map((capability) => (
            <article className="capability-card" key={capability.title}>
              <h3>{capability.title}</h3>
              <p>{capability.description}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="state-section" aria-labelledby="state-title">
        <div>
          <p className="section-kicker">EXPLICIT OUTCOMES</p>
          <h2 id="state-title">Every uncertain state has a clear response.</h2>
        </div>
        <div className="state-grid">
          <SystemState
            title="Not enough evidence"
            description="The answer pauses until an authorized source supports the request."
            state="refusal"
          />
          <SystemState
            title="Partial support"
            description="Supported claims remain visible while gaps are called out explicitly."
            state="partial"
          />
          <SystemState
            title="Processing error"
            description="Failures stay actionable without exposing internal implementation details."
            state="error"
          />
        </div>
      </section>
    </main>
  );
}
