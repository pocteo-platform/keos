import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useApp } from "@/lib/store";
import { api } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  COMPANY_NAME,
  PLATFORM_NAME,
  PRODUCT_NAME,
  TAGLINE_EN,
  TAGLINE_FR,
} from "@/lib/branding";

// Paste-the-token screen. Verifies by calling /api/mode; on 401 the API
// client clears the store and the user lands back here.
export default function LoginPage() {
  const { t } = useTranslation();
  const setToken = useApp((s) => s.setToken);
  const navigate = useNavigate();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setError(null);
    setBusy(true);
    const tok = value.replace(/^SHP_API_TOKEN=/, "").trim();
    setToken(tok);
    try {
      await api.getMode();
      navigate("/cluster", { replace: true });
    } catch {
      setError(t("login.invalid"));
      setToken(null);
    } finally {
      setBusy(false);
    }
  }

  const locale = useApp((s) => s.locale);
  const tagline = locale === "fr" ? TAGLINE_FR : TAGLINE_EN;

  return (
    <div
      className="grid min-h-screen place-items-center p-6"
      // Subtle brand-toned background — brand-navy fading to background.
      // Pure CSS gradient, no images, no asset request.
      style={{
        background:
          "radial-gradient(circle at 30% 20%, rgba(21,17,122,0.06) 0%, transparent 60%), radial-gradient(circle at 70% 80%, rgba(8,6,70,0.08) 0%, transparent 55%), hsl(var(--background))",
      }}
    >
      <div className="w-full max-w-md space-y-6">
        {/* Logo + product hierarchy. Three lines of typography:
            (1) "Keos · edge OS" — the platform,
            (2) "SRE Agent" — this product,
            (3) tagline — the one-liner. */}
        <div className="flex flex-col items-center text-center">
          <img
            src="/keos-shape.jpg"
            alt={PLATFORM_NAME}
            width={64}
            height={64}
            className="mb-4 select-none rounded-md"
            draggable={false}
          />
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-bold tracking-tight">{PLATFORM_NAME}</span>
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              edge OS
            </span>
          </div>
          <div className="mt-1 text-base font-medium text-foreground">
            {PRODUCT_NAME.replace(`${PLATFORM_NAME} `, "")}
          </div>
          <p className="mt-3 max-w-sm text-xs leading-snug text-muted-foreground">{tagline}</p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>{t("login.title")}</CardTitle>
            <CardDescription>{t("login.body")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="tok">SHP_API_TOKEN</Label>
              <Input
                id="tok"
                type="password"
                placeholder={t("login.placeholder")}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void submit();
                }}
              />
            </div>
            {error ? <p className="text-sm text-danger">{error}</p> : null}
            <Button onClick={() => void submit()} disabled={busy || !value} className="w-full">
              {t("login.submit")}
            </Button>
          </CardContent>
        </Card>

        {/* Footer attribution: company on the left, docs link on the right. */}
        <div className="flex items-center justify-between text-[10px] uppercase tracking-wider text-muted-foreground">
          <div className="flex items-center gap-1.5">
            <img src="/pocteo-platform.png" alt="" height={14} className="opacity-70" />
            <span>
              by <span className="font-semibold tracking-normal">{COMPANY_NAME}</span>
            </span>
          </div>
          <a
            href="http://localhost:3000"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-foreground"
          >
            docs ↗
          </a>
        </div>
      </div>
    </div>
  );
}
