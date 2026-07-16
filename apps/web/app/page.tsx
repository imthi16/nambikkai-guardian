const capabilities = [
  "Tamil, Tanglish, and English document understanding",
  "Hybrid retrieval with exact page and span citations",
  "Claim verification and evidence-based refusal",
  "Prompt-injection detection and document quarantine",
];

export default function HomePage() {
  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "72px 24px" }}>
      <p style={{ fontWeight: 700, letterSpacing: 1.2 }}>NAMBIKKAI GUARDIAN</p>
      <h1 style={{ fontSize: 56, lineHeight: 1.05, margin: "16px 0" }}>
        Document AI that proves its answers.
      </h1>
      <p style={{ fontSize: 20, lineHeight: 1.6, maxWidth: 760 }}>
        A secure, evidence-first intelligence platform for Tamil, Tanglish, and English
        documents. The system cites its sources, verifies claims, and refuses unsupported
        answers.
      </p>

      <section style={{ marginTop: 48 }}>
        <h2>Planned MVP capabilities</h2>
        <ul style={{ fontSize: 18, lineHeight: 1.8 }}>
          {capabilities.map((capability) => (
            <li key={capability}>{capability}</li>
          ))}
        </ul>
      </section>
    </main>
  );
}
