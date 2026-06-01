/// <reference types="vitest/globals" />
/// <reference types="@testing-library/jest-dom/vitest" />

// Extends Vitest's expect() with jest-dom's `toBeInTheDocument`,
// `toHaveTextContent`, etc. Loaded by tsc for any file under
// `src/__tests__/` thanks to tsconfig.app.json's include glob.
