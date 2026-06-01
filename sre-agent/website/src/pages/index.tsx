import React from "react";
import clsx from "clsx";
import Link from "@docusaurus/Link";
import useDocusaurusContext from "@docusaurus/useDocusaurusContext";
import Layout from "@theme/Layout";

// ─────────────────────────────────────────────────────────────────────────────
// Landing page — three sections:
//   1. Hero       — tagline + two CTAs (Quick start / Architecture)
//   2. Features   — four cards summarizing the loop's design pillars
//   3. Status     — release candidate banner with link to the release notes
//
// Keep this file static. Anything that needs to read live content (e.g. a
// "latest release version" badge) belongs in docusaurus.config.ts so it
// stays bundled with the build.
// ─────────────────────────────────────────────────────────────────────────────

function HomepageHero(): React.JSX.Element {
  const { siteConfig } = useDocusaurusContext();
  return (
    <header className={clsx("hero hero--primary")} style={{ padding: "4rem 2rem" }}>
      <div className="container" style={{ textAlign: "center", maxWidth: 880 }}>
        <h1 className="hero__title">{siteConfig.title}</h1>
        <p className="hero__subtitle">{siteConfig.tagline}</p>
        <p style={{ marginTop: "1.5rem", fontSize: "1.05rem", lineHeight: 1.55 }}>
          A policy-gated autonomous SRE for Kubernetes. Observes events, diagnoses with
          retrieval-augmented context, plans one remediation, gates it through OPA, executes under
          scoped RBAC, verifies the cluster healed, and writes a tamper-evident HMAC audit record —
          every time.
        </p>
        <div style={{ display: "flex", gap: "1rem", justifyContent: "center", marginTop: "2rem" }}>
          <Link className="button button--secondary button--lg" to="/docs/USER_GUIDE">
            Quick start →
          </Link>
          <Link className="button button--outline button--lg" to="/docs/ARCHITECTURE">
            Architecture
          </Link>
        </div>
      </div>
    </header>
  );
}

interface Feature {
  title: string;
  body: React.ReactNode;
}

const FEATURES: Feature[] = [
  {
    title: "Observe → Predict → Heal → Learn",
    body: (
      <>
        A LangGraph state machine with 8 deterministic nodes. Every step audited, every action
        gated by a Rego severity matrix you can read in under a minute.
      </>
    ),
  },
  {
    title: "Policy as code — Rego, not prompts",
    body: (
      <>
        The severity × action matrix lives in <code>policies/agent.rego</code>. OPA decides
        allow/deny; the LLM never has the keys. <code>AgentMode</code> CRD is a hot-flippable
        kill switch.
      </>
    ),
  },
  {
    title: "Tamper-evident audit",
    body: (
      <>
        Every node entry, every policy decision, every kubectl call is HMAC-chained. Rotate
        the key and the chain breaks loudly — see the audit-tampering runbook.
      </>
    ),
  },
  {
    title: "Edge-ready, GPU-optional",
    body: (
      <>
        CPU-only by default; multi-arch images (amd64 + arm64) for k3s on Jetson or Talos.
        Helm chart toggles GPU acceleration when available.
      </>
    ),
  },
];

function HomepageFeatures(): React.JSX.Element {
  return (
    <section style={{ padding: "4rem 0" }}>
      <div className="container">
        <div className="row">
          {FEATURES.map((feat) => (
            <div key={feat.title} className="col col--6" style={{ marginBottom: "2rem" }}>
              <h3 style={{ marginTop: 0 }}>{feat.title}</h3>
              <p>{feat.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function StatusBanner(): React.JSX.Element {
  return (
    <section style={{ padding: "2rem 0", borderTop: "1px solid var(--ifm-color-emphasis-300)" }}>
      <div className="container" style={{ textAlign: "center" }}>
        <p style={{ margin: 0 }}>
          Status: <strong>v1.0.0-rc.1</strong> — first release candidate.{" "}
          <Link to="/docs/RELEASE_NOTES_1.0.0-rc.1">Read the release notes →</Link>
        </p>
      </div>
    </section>
  );
}

export default function Home(): React.JSX.Element {
  return (
    <Layout
      title="The Autonomous AI SRE Platform"
      description="Policy-gated, audit-first SRE agent for Kubernetes — Observe, Predict, Heal, Learn."
    >
      <HomepageHero />
      <main>
        <HomepageFeatures />
        <StatusBanner />
      </main>
    </Layout>
  );
}
