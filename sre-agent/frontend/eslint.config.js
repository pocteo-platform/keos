// ESLint flat config — Keos frontend.
//
// V1 goal: catch real bugs (no-undef, hooks rules, unused vars) without
// drowning the build in style noise. Phase 10.7 will add jsx-a11y and
// prettier on top once we've done a clean-up pass on existing files.
//
// `npm run lint` runs this against the whole `src/` tree.

import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    // Skip generated + vendored output.
    ignores: ["dist/**", "node_modules/**", ".vite/**", "coverage/**"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.es2022 },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      // Hooks: hard rules. Violations almost always indicate real bugs.
      ...reactHooks.configs.recommended.rules,

      // React Fast Refresh — warn when a non-component is exported from a
      // tsx file (breaks HMR).
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],

      // Unused vars: warn (errors would block CI today; 10.7 promotes to
      // error after the cleanup pass).
      "@typescript-eslint/no-unused-vars": [
        "warn",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],

      // `any` is the loose escape hatch. After Phase 10.7 cleanup pass +
      // unit-test coverage, we tighten to `error` to keep new code clean.
      // Existing legitimate cases get explicit `// eslint-disable-next-line`.
      "@typescript-eslint/no-explicit-any": "error",

      // We use `console.warn` / `console.error` in dev paths; only block
      // raw `console.log`.
      "no-console": ["warn", { allow: ["warn", "error", "info"] }],
    },
  },
  {
    // Tests + config files: looser still.
    files: ["**/*.config.{js,ts}", "**/*.test.{ts,tsx}", "tests/**/*"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": "off",
    },
  },
);
