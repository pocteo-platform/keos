import type { Config } from "@docusaurus/types";
import type * as Preset from "@docusaurus/preset-classic";
import { themes as prismThemes } from "prism-react-renderer";

// ─────────────────────────────────────────────────────────────────────────────
// Repo coordinates — drive the "Edit this page" links + GitHub button.
// Update if the repo moves.
// ─────────────────────────────────────────────────────────────────────────────
const GITHUB_ORG = "pocteo-platform";
const GITHUB_REPO = "keos";
const REPO_URL = `https://github.com/${GITHUB_ORG}/${GITHUB_REPO}`;
const EDIT_BASE = `${REPO_URL}/edit/main/sre-agent`;

// Where on the web this gets served. Defaults assume GitHub Pages on the
// project's gh-pages branch. Override via env if you host elsewhere.
const URL = process.env.DOCS_URL ?? `https://${GITHUB_ORG}.github.io`;
const BASE_URL = process.env.DOCS_BASE_URL ?? `/${GITHUB_REPO}/`;

const config: Config = {
  title: "Keos SRE Agent",
  tagline:
    "The autonomous SRE that lives inside every Keos edge node — by Pocteo Platform.",
  favicon: "img/favicon.ico",

  url: URL,
  baseUrl: BASE_URL,
  trailingSlash: false,

  organizationName: GITHUB_ORG,
  projectName: GITHUB_REPO,
  deploymentBranch: "gh-pages",

  // Treat broken links as errors in CI; warn locally so iteration stays fast.
  onBrokenLinks: process.env.CI ? "throw" : "warn",
  onBrokenMarkdownLinks: "warn",

  // ─── Internationalization ────────────────────────────────────────────────
  // Mirrors the SPA's EN/FR setup. French strings + doc translations live
  // under `i18n/fr/`. Until those are populated, FR falls back to EN.
  i18n: {
    defaultLocale: "en",
    locales: ["en", "fr"],
    localeConfigs: {
      en: { label: "English", htmlLang: "en-US" },
      fr: { label: "Français", htmlLang: "fr-FR" },
    },
  },

  markdown: {
    // Treat `.md` files as plain CommonMark (lenient — autolinks like
    // `<https://example.com>`, raw `<` in prose, and HTML comments
    // all work) and reserve full MDX parsing for `.mdx` files. Without
    // this, every angle bracket in the existing runbooks would need to
    // be escaped. Set per-file via front-matter `format: mdx` when JSX
    // components are needed inside a `.md` page.
    format: "detect",
    // Mermaid disabled — Docusaurus 3.4-3.8 has an SSR bug where the
    // mermaid renderer calls useColorMode outside the ColorModeProvider.
    // Diagrams ship as plain `text title="Mermaid…"` code blocks for
    // now; readers can paste into https://mermaid.live to render.
    mermaid: false,
  },

  plugins: [
    [
      // Local-only full-text search. Indexes EN + FR. No external
      // service, no API keys — works in air-gapped deployments.
      require.resolve("@easyops-cn/docusaurus-search-local"),
      {
        hashed: true,
        language: ["en", "fr"],
        highlightSearchTermsOnTargetPage: true,
        indexDocs: true,
        indexBlog: false,
        indexPages: true,
      },
    ],
  ],

  presets: [
    [
      "classic",
      {
        docs: {
          // The docs site is self-contained — its own curated `docs/`
          // tree under `website/`. Content is hand-authored or copied
          // from the repo-level markdown, but the website never reaches
          // outside its directory. Keeps the repo-meta files
          // (CONTRIBUTING.md, SECURITY.md, etc.) in their conventional
          // GitHub locations without forcing them to also be docs pages.
          path: "docs",
          routeBasePath: "docs",
          sidebarPath: "./sidebars.ts",
          editUrl: ({ docPath }) => `${EDIT_BASE}/website/docs/${docPath}`,
          showLastUpdateTime: true,
          showLastUpdateAuthor: true,
        },
        blog: false,
        theme: {
          customCss: "./src/css/custom.css",
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    // Open Graph + Twitter cards. Replace with a real screenshot when ready.
    image: "img/social-card.png",
    colorMode: {
      defaultMode: "light",
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: "Keos",
      logo: {
        alt: "Keos logo",
        src: "img/logo.svg",
      },
      items: [
        {
          type: "docSidebar",
          sidebarId: "userGuide",
          position: "left",
          label: "User Guide",
        },
        {
          type: "docSidebar",
          sidebarId: "operations",
          position: "left",
          label: "Operations",
        },
        {
          type: "docSidebar",
          sidebarId: "reference",
          position: "left",
          label: "Reference",
        },
        {
          to: "/docs/runbooks/",
          label: "Runbooks",
          position: "left",
        },
        {
          type: "localeDropdown",
          position: "right",
        },
        {
          href: REPO_URL,
          label: "GitHub",
          position: "right",
        },
      ],
    },
    footer: {
      style: "dark",
      links: [
        {
          title: "Docs",
          items: [
            { label: "Quickstart", to: "/docs/getting-started/quickstart" },
            { label: "Architecture", to: "/docs/architecture/overview" },
            { label: "Runbooks", to: "/docs/runbooks/" },
            { label: "Config reference", to: "/docs/configuration/environment-variables" },
          ],
        },
        {
          title: "Community",
          items: [
            { label: "GitHub", href: REPO_URL },
            { label: "Issues", href: `${REPO_URL}/issues` },
            { label: "Discussions", href: `${REPO_URL}/discussions` },
          ],
        },
        {
          title: "Project",
          items: [
            { label: "Positioning", href: `${REPO_URL}/blob/main/sre-agent/POSITIONING.md` },
            { label: "Changelog", href: `${REPO_URL}/blob/main/sre-agent/CHANGELOG.md` },
            { label: "Security policy", href: `${REPO_URL}/blob/main/sre-agent/SECURITY.md` },
            { label: "Contributing", href: `${REPO_URL}/blob/main/sre-agent/CONTRIBUTING.md` },
          ],
        },
      ],
      copyright: `Pocteo Platform · Keos edge OS · SRE Agent v1.0.0-rc.1`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ["bash", "yaml", "json", "python", "typescript", "rego", "tsx", "diff"],
    },
    docs: {
      sidebar: {
        hideable: true,
        autoCollapseCategories: false,
      },
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
