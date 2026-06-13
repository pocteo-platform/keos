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

  // Mirrored pages link to the canonical UPPERCASE markdown filenames
  // (KAIROS_JETSON.md, AIRGAP_INSTALL.md, etc.) under sre-agent/docs/ —
  // those are the source of truth operators read. Docusaurus's slug-
  // style tree can't resolve them, so warn instead of throw. Re-enable
  // throw once we've automated the docs/ → website/ link rewrite.
  onBrokenLinks: "warn",
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
    // Mermaid: the docs use ```mermaid fences (they render natively on
    // GitHub — the single source). To render them on THIS site too, bump
    // Docusaurus to >= 3.9 (fixes the useColorMode SSR bug present in
    // 3.4–3.8) then set `mermaid: true` and add
    // `themes: ["@docusaurus/theme-mermaid"]`. Kept false here because the
    // pinned 3.7.0 drops the page on SSR. See the LICENSE/upgrade follow-up.
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
          // SINGLE SOURCE OF TRUTH (Phase 24): the site renders the 6 canonical
          // docs in `sre-agent/docs/` directly — there is no second copy under
          // `website/docs/`. Edit the markdown in `sre-agent/docs/`; the site
          // and GitHub stay in lockstep forever.
          path: "../docs",
          routeBasePath: "docs",
          sidebarPath: "./sidebars.ts",
          editUrl: ({ docPath }) => `${EDIT_BASE}/docs/${docPath}`,
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
        src: "img/keos-shape.jpg",
      },
      items: [
        {
          type: "docSidebar",
          sidebarId: "docs",
          position: "left",
          label: "Docs",
        },
        { to: "/docs/cli", label: "CLI", position: "left" },
        { to: "/docs/runbooks/", label: "Runbooks", position: "left" },
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
            { label: "User Guide", to: "/docs/user-guide" },
            { label: "Architecture", to: "/docs/architecture" },
            { label: "Installation", to: "/docs/installation" },
            { label: "CLI reference", to: "/docs/cli" },
            { label: "Runbooks", to: "/docs/runbooks/" },
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
